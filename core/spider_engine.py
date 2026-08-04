from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import inspect
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import urlsplit

from playwright.async_api import TimeoutError as PlaywrightTimeoutError
from playwright.async_api import async_playwright

from core.captures import (
    CaptureConfigurationError,
    PageCaptureSession,
    RequiredCaptureError,
    parse_capture_specs,
)
from core.extraction_pipeline import FieldExtractionPipeline, HealingResult
from core.llm_repair import LLMRepair
from core.quality_gate import QualityGate
from core.repair_persistence import RepairPersistence


logger = logging.getLogger(__name__)
_BROWSER_CLEANUP_TIMEOUT_SECONDS = 2.0


def _literal_config_bool(value: Any, *, default: bool, label: str) -> bool:
    """Accept only YAML/JSON booleans for security-relevant switches."""

    if value is None:
        return default
    if not isinstance(value, bool):
        raise TypeError(f"{label} must be true or false, not {type(value).__name__}.")
    return value


async def _cancel_and_drain(task: "asyncio.Task[Any]") -> None:
    """Cancel a cleanup task and retrieve its terminal state.

    Merely attaching a done callback leaves the task pending while an outer
    ``wait_for`` cancellation tears down the event loop.  Playwright then keeps
    its driver pipes alive and an otherwise completed test/process can hang.
    Browser close coroutines are cancellation-aware, so draining them here is
    both bounded by their cancellation and prevents that orphaned-task state.
    """

    task.cancel()
    try:
        await task
    except BaseException:
        pass


async def _run_browser_cleanup(cleanup: Any, label: str) -> None:
    """Run one Playwright cleanup coroutine with bounded cancellation."""

    close_task = asyncio.create_task(cleanup, name=f"crawler-close:{label}")
    try:
        done, _ = await asyncio.wait(
            {close_task},
            timeout=_BROWSER_CLEANUP_TIMEOUT_SECONDS,
        )
    except asyncio.CancelledError:
        await _cancel_and_drain(close_task)
        raise
    if not done:
        await _cancel_and_drain(close_task)
        logger.warning(
            "Timed out after %.1fs while closing %s.",
            _BROWSER_CLEANUP_TIMEOUT_SECONDS,
            label,
        )
        return
    try:
        close_task.result()
    except asyncio.CancelledError:
        logger.warning("Closing %s was cancelled.", label)
    except Exception as exc:  # pragma: no cover - cleanup guard
        logger.warning("Could not close %s cleanly: %s", label, exc)


async def _close_browser_resource(resource: Any, label: str) -> None:
    """Attempt browser cleanup without allowing a stuck close to hang the run."""

    await _run_browser_cleanup(resource.close(), label)


async def _close_playwright_manager(manager: Any) -> None:
    """Stop Playwright even if cancellation interrupted ``__aenter__``.

    An ``async with`` statement does not call ``__aexit__`` when its enter step
    is cancelled.  Short assistant deadlines can expire while Chromium is
    starting, leaving Playwright's connection and initialization tasks alive.
    Managing the context explicitly closes that gap.
    """

    await _run_browser_cleanup(
        manager.__aexit__(None, None, None),
        "Playwright driver",
    )

try:
    from scrapling.parser import Selector as ScraplingSelector
except Exception:  # pragma: no cover
    try:
        from scrapling import Selector as ScraplingSelector
    except Exception:  # pragma: no cover
        ScraplingSelector = None  # type: ignore[assignment]

try:
    from scrapling.core.storage import SQLiteStorageSystem as ScraplingSQLiteStorage
except Exception:  # pragma: no cover
    ScraplingSQLiteStorage = None  # type: ignore[assignment]

class GenericSpider:
    def __init__(
        self,
        config: Dict[str, Any],
        network_policy: Any = None,
        *,
        experience_store: Any = None,
    ):
        self.config = config or {}
        # The approval assistant supplies a restrictive policy. Direct, standalone
        # callers keep the legacy configuration surface and behavior unchanged.
        self.network_policy = network_policy
        self.name = self.config.get("name", "generic-spider")
        self.start_urls = self._collect_start_urls()
        self.browser_config = self.config.get("browser", {})
        self.request_config = self.config.get("request", {})
        self.pagination = self.config.get("pagination", {})
        self.actions = self.config.get("actions", [])
        self.fields = self.config.get("fields", [])
        self.capture_specs = parse_capture_specs(self.config.get("captures"))
        action_result_keys = {
            result_key.strip()
            for action in self.actions
            if isinstance(action, dict)
            and isinstance((result_key := action.get("result_key")), str)
            and result_key.strip()
        }
        capture_action_conflicts = sorted(
            {spec.name for spec in self.capture_specs} & action_result_keys
        )
        if capture_action_conflicts:
            raise CaptureConfigurationError(
                "capture names cannot reuse action result_key values: "
                + ", ".join(capture_action_conflicts)
            )
        self.payload_key = self.config.get("payload_key", "payload")
        self.enable_adaptive = _literal_config_bool(
            self.config.get("enable_adaptive"),
            default=True,
            label="enable_adaptive",
        )
        self.llm_config = self.config.get("llm", {})
        if not isinstance(self.llm_config, dict):
            raise TypeError("llm must be a mapping.")
        self.enable_llm_repair = _literal_config_bool(
            self.llm_config.get("enable_repair"),
            default=False,
            label="llm.enable_repair",
        )
        self.llm_repair = LLMRepair(self.llm_config) if self.enable_llm_repair else None
        repair_config = self.config.get("repair_memory", {})
        if not isinstance(repair_config, dict):
            repair_config = {}
        repair_enabled = _literal_config_bool(
            repair_config.get("enabled"),
            default=False,
            label="repair_memory.enabled",
        )
        repair_path = repair_config.get("path")
        self.quality_gate = QualityGate()
        self.repair_memory = RepairPersistence(
            str(repair_path) if repair_path else None,
            enabled=repair_enabled,
        )
        self.extraction_pipeline = FieldExtractionPipeline(
            quality_gate=self.quality_gate,
            repair_memory=self.repair_memory,
        )
        self._adapter_post_process = None
        self.experience_store = experience_store
        self.repair_episode_ids: List[str] = []
        self.scrapling_cache: Dict[int, Dict[str, Any]] = {}
        self._scrapling_storages: Dict[str, Any] = {}
        self.retain_full_episode_content = _literal_config_bool(
            self.config.get("retain_full_episode_content"),
            default=False,
            label="retain_full_episode_content",
        )
        self.max_pages = int(self.pagination.get("max_pages", 1))
        self.results: List[Dict[str, Any]] = []

    def set_experience_store(self, experience_store: Any) -> None:
        """Attach an explicitly constructed local store.

        No store is created by ``GenericSpider`` itself; callers retain control
        of the path and lifetime.
        """
        self.experience_store = experience_store

    @classmethod
    def from_adapter(
        cls,
        adapter: Any,
        start_url: str,
        *,
        network_policy: Any = None,
        **overrides: Any,
    ) -> "GenericSpider":
        """Build a spider from an adapter and enable its post-processing hook.

        Direct ``GenericSpider(adapter.to_config(...))`` construction remains a
        plain configuration run.  Adapter post-processing is intentionally only
        attached through this explicit entry point.
        """
        spider = cls(
            adapter.to_config(start_url, **overrides),
            network_policy=network_policy,
        )
        spider._adapter_post_process = adapter.post_process
        return spider

    def _collect_start_urls(self) -> List[str]:
        urls: List[str] = []
        seen: set[str] = set()

        def append_once(value: Any) -> None:
            if not isinstance(value, str):
                return
            normalized = value.strip()
            if normalized and normalized not in seen:
                seen.add(normalized)
                urls.append(normalized)

        start_url = self.config.get("start_url")
        append_once(start_url)
        for value in self.config.get("start_urls", []) or []:
            append_once(value)
        return urls

    @staticmethod
    def _readable_path(path_value: Any) -> str:
        if path_value is None:
            return ""
        if isinstance(path_value, str):
            return path_value.strip()
        return str(path_value)

    @staticmethod
    def _ensure_text(value: Any) -> str:
        if value is None:
            return ""
        if isinstance(value, str):
            return value.strip()
        return str(value).strip()

    @staticmethod
    def _ensure_records(result: Any) -> List[Dict[str, Any]]:
        if result is None:
            return []
        if isinstance(result, list):
            return [row if isinstance(row, dict) else {"value": row} for row in result]
        if isinstance(result, dict):
            return [result]
        return [{"value": result}]

    @staticmethod
    def _resolve_json_path(base: Any, path: str) -> Any:
        if base is None or not path:
            return ""
        current = base
        for token in path.split("."):
            if current is None:
                return ""
            if isinstance(current, dict):
                if token not in current:
                    return ""
                current = current[token]
                continue
            if isinstance(current, list):
                try:
                    index = int(token)
                    current = current[index]
                    continue
                except (ValueError, IndexError):
                    return ""
            return ""
        return current

    def _read_from_context(self, context: Dict[str, Any], path: str) -> Any:
        clean = self._readable_path(path)
        if not clean:
            return ""
        if clean.startswith("payload.") and self.payload_key in context:
            return self._resolve_json_path(context.get(self.payload_key, {}), clean.split(".", 1)[1])

        prefix = clean.split(".", 1)[0]
        if prefix in context:
            if "." in clean:
                return self._resolve_json_path(context[prefix], clean.split(".", 1)[1])
            return context[prefix]
        if self.payload_key in context:
            return self._resolve_json_path(context[self.payload_key], clean)
        return ""

    async def _prepare_page(self, page) -> None:
        timeout = int(self.request_config.get("timeout_ms", 30000))
        wait_until = self.request_config.get("wait_until", "domcontentloaded")
        if wait_selector := self.request_config.get("wait_for_selector"):
            await page.wait_for_selector(wait_selector, timeout=timeout)
        else:
            try:
                await page.wait_for_load_state(wait_until=wait_until, timeout=timeout)
            except TypeError:
                # Playwright versions before async API signature changes use `state` instead of `wait_until`.
                await page.wait_for_load_state(state=wait_until, timeout=timeout)

    async def _goto_page(self, page: Any, url: str) -> None:
        """Navigate with the configured bounded wait instead of Playwright defaults."""

        goto = getattr(page, "goto")
        candidate_kwargs: Dict[str, Any] = {
            "timeout": int(self.request_config.get("timeout_ms", 30000)),
        }
        if "wait_until" in self.request_config:
            candidate_kwargs["wait_until"] = self.request_config["wait_until"]
        kwargs = self._select_kwargs(
            goto,
            candidate_kwargs,
        )
        await goto(url, **kwargs)

    @staticmethod
    def _select_kwargs(method, candidate_kwargs: Dict[str, Any]) -> Dict[str, Any]:
        if method is None:
            return {}
        try:
            sig = inspect.signature(method)
        except (TypeError, ValueError):  # pragma: no cover
            return {}

        if any(param.kind == inspect.Parameter.VAR_KEYWORD for param in sig.parameters.values()):
            return candidate_kwargs

        return {k: v for k, v in candidate_kwargs.items() if k in sig.parameters}

    def _scrapling_storage_for_url(self, url: str) -> Any:
        """Return per-spider, in-memory adaptive state for one target host.

        Scrapling's default adaptive storage is a SQLite file inside its
        installed package. That implicit write is inappropriate when repair
        memory is disabled and can also fail in read-only installations. A
        private ``:memory:`` database preserves within-run relocation without
        creating or reusing an unapproved history file.
        """

        if ScraplingSQLiteStorage is None:
            return None
        try:
            parsed = urlsplit(url)
            storage_key = (parsed.hostname or "default").rstrip(".").lower()
        except ValueError:
            storage_key = "default"
        if storage_key in self._scrapling_storages:
            return self._scrapling_storages[storage_key]

        # Bypass Scrapling's process-global lru_cache wrapper so adaptive state
        # cannot leak between independent GenericSpider runs.
        storage_factory = getattr(ScraplingSQLiteStorage, "__wrapped__", None)
        if storage_factory is None:
            logger.warning("Scrapling storage API is incompatible; adaptive extraction is disabled.")
            return None
        try:
            storage = storage_factory(storage_file=":memory:", url=url)
        except Exception as exc:  # pragma: no cover - optional dependency guard
            logger.warning("Could not initialize in-memory Scrapling storage: %s", exc)
            return None
        self._scrapling_storages[storage_key] = storage
        return storage

    def _close_scrapling_storages(self) -> None:
        # Scrapling's storage destructor owns its connection cleanup and is not
        # idempotent. Drop selector references first, then the private storage
        # references, so it closes exactly once without a process-global cache.
        self.scrapling_cache.clear()
        self._scrapling_storages.clear()

    async def _extract_playwright_field(
        self,
        target: Any,
        selector: str,
        attr: str,
    ) -> str:
        if target is None or not selector:
            return ""

        try:
            element = await target.query_selector(selector)
        except Exception as exc:  # pragma: no cover
            logger.debug("Playwright 选择器失败: selector=%s, target=%s, error=%s", selector, type(target).__name__, exc)
            return ""

        if element is None:
            return ""

        try:
            if attr:
                value = await element.get_attribute(attr)
            else:
                value = await element.inner_text()
        except Exception as exc:  # pragma: no cover
            logger.debug("Playwright 提取字段失败: selector=%s, attr=%s, error=%s", selector, attr, exc)
            return ""

        return self._ensure_text(value)

    async def _collect_repair_context(
        self,
        page,
        context_node: Optional[Any] = None,
    ) -> str:
        html = ""
        if context_node is not None:
            try:
                html = await context_node.inner_html()
            except Exception:
                html = ""
        if not html:
            try:
                html = await page.content()
            except Exception as exc:  # pragma: no cover
                logger.debug("读取页面 HTML 失败: %s", exc)
                return ""
        return html or ""

    async def _ensure_scrapling_selector(self, page, node: Any = None):
        if ScraplingSelector is None:
            logger.warning("Scrapling 未安装，无法启用自适应提取。请先安装 scrapling。")
            return None

        cache_key = f"{id(page)}:{id(node) if node else 0}"
        if page_id_cache := self.scrapling_cache.get(id(page)):
            if cache_key in page_id_cache:
                return page_id_cache[cache_key]

        try:
            html = await (node.inner_html() if node is not None else page.content())
        except Exception as exc:  # pragma: no cover
            logger.debug("读取 HTML 用于 Scrapling 提取失败: %s", exc)
            return None

        if not html:
            logger.debug("Scrapling 输入 HTML 为空，无法定位：selector_context=%s", "node" if node else "page")
            return None

        if node is not None:
            html = f"<div>{html}</div>"

        page_url = str(getattr(page, "url", "default") or "default")
        storage = self._scrapling_storage_for_url(page_url)
        if storage is None:
            return None
        try:
            selector_obj = ScraplingSelector(
                html,
                adaptive=self.enable_adaptive,
                url=page_url,
                _storage=storage,
            )
        except Exception as exc:  # pragma: no cover
            logger.warning("Scrapling 选择器初始化失败: %s", exc)
            return None

        page_cache = self.scrapling_cache.setdefault(id(page), {})
        page_cache[cache_key] = selector_obj
        return selector_obj

    async def _extract_from_scrapling(
        self,
        page,
        selector: str,
        attr: str,
        node: Optional[Any] = None,
        identifier: Optional[str] = None,
    ) -> str:
        selector_obj = await self._ensure_scrapling_selector(page, node=node)
        if selector_obj is None:
            return ""

        css_method = getattr(selector_obj, "css", None)
        if not callable(css_method):
            return ""

        identifier = identifier or selector
        base_kwargs: Dict[str, Any] = {}
        if self._select_kwargs(css_method, {"identifier": identifier}):
            base_kwargs["identifier"] = identifier
        if self._select_kwargs(css_method, {"auto_save": True}):
            base_kwargs["auto_save"] = True
        try:
            result = css_method(selector, **base_kwargs)
        except Exception as exc:  # pragma: no cover
            logger.debug("Scrapling 基础选择器执行失败: selector=%s, error=%s", selector, exc)
            return ""

        value = self._resolve_scrapling_value(result, attr=attr)
        if value:
            return value

        if not self.enable_adaptive:
            return ""

        adaptive_kwargs = {}
        if self._select_kwargs(css_method, {"identifier": identifier}):
            adaptive_kwargs["identifier"] = identifier
        if self._select_kwargs(css_method, {"auto_save": False}):
            adaptive_kwargs["auto_save"] = False
        if self._select_kwargs(css_method, {"adaptive": True}):
            adaptive_kwargs["adaptive"] = True

        try:
            result = css_method(selector, **adaptive_kwargs)
        except TypeError:
            result = css_method(selector)
        except Exception as exc:  # pragma: no cover
            logger.debug("Scrapling adaptive 选择器执行失败: selector=%s, error=%s", selector, exc)
            return ""

        return self._resolve_scrapling_value(result, attr=attr)

    def _iter_scrapling_nodes(self, result: Any) -> List[Any]:
        if result is None:
            return []
        if isinstance(result, (str, bytes, int, float, bool)):
            return [result]

        if hasattr(result, "first"):
            first = getattr(result, "first")
            if first is not None:
                return [first]

        try:
            return list(result)
        except TypeError:
            return [result]

    def _resolve_scrapling_value(self, result: Any, attr: str) -> str:
        for item in self._iter_scrapling_nodes(result):
            if attr:
                value = self._extract_scrapling_attr(item, attr)
            else:
                value = self._extract_scrapling_text(item)
            if value:
                return value
        return ""

    @staticmethod
    def _extract_scrapling_attr(item: Any, attr: str) -> str:
        if not attr:
            return ""

        if isinstance(item, dict):
            attrib = item.get("attrib")
            if isinstance(attrib, dict):
                return GenericSpider._ensure_text(attrib.get(attr, ""))
            if "attributes" in item and isinstance(item["attributes"], dict):
                return GenericSpider._ensure_text(item["attributes"].get(attr, ""))
            return ""

        if hasattr(item, "attrib"):
            try:
                attrib = getattr(item, "attrib")
                if isinstance(attrib, dict):
                    return GenericSpider._ensure_text(attrib.get(attr, ""))
            except Exception:
                pass
        return ""

    @staticmethod
    def _extract_scrapling_text(item: Any) -> str:
        if item is None:
            return ""

        if isinstance(item, (str, bytes)):
            return GenericSpider._ensure_text(item)

        if isinstance(item, dict):
            if isinstance(item.get("text"), (str, bytes)):
                return GenericSpider._ensure_text(item.get("text", ""))
            return GenericSpider._ensure_text(item.get("data", ""))

        for attr_name in ("text", "get", "getall", "text_content", "inner_html"):
            value = getattr(item, attr_name, None)
            if value is None:
                continue
            if callable(value):
                try:
                    text = value()
                except Exception:
                    continue
            else:
                text = value
            if attr_name == "getall":
                values = list(text)
                if values:
                    return GenericSpider._ensure_text(values[0])
            else:
                if text is not None:
                    return GenericSpider._ensure_text(text)
        return GenericSpider._ensure_text(getattr(item, "__str__")())

    async def _extract_field_adaptive(
        self,
        page,
        field: Dict[str, Any],
        context_node: Optional[Any] = None,
        capture_context: Optional[Dict[str, Any]] = None,
    ) -> str:
        selector = self._readable_path(field.get("selector"))

        attr = self._readable_path(field.get("attr"))
        field_name = self._readable_path(field.get("name"))
        field_description = self._readable_path(field.get("description")) or field_name
        # Page-scoped extraction uses the Page itself for Playwright selectors,
        # but Scrapling needs the complete page HTML rather than an element's
        # ``inner_html``. Treat an explicitly passed Page as no node context.
        scrapling_node = None if context_node is page else context_node

        async def extract_selector(candidate: str, candidate_attr: Optional[str]) -> str:
            return await self._extract_playwright_field(
                context_node or page,
                candidate,
                candidate_attr or "",
            )

        async def observe_configured(candidate: str) -> None:
            if not self.enable_adaptive:
                return
            # Preserve a successful selector as Scrapling's baseline so a later
            # page revision has an adaptive reference instead of starting cold.
            await self._extract_from_scrapling(
                page=page,
                selector=candidate,
                attr=attr,
                node=scrapling_node,
                identifier=field_name or candidate,
            )

        async def extract_adaptive(
            candidate: str,
            candidate_attr: Optional[str],
            identifier: str,
        ) -> str:
            return await self._extract_from_scrapling(
                page=page,
                selector=candidate,
                attr=candidate_attr or "",
                node=scrapling_node,
                identifier=identifier or candidate,
            )

        async def generate_llm_candidate() -> str:
            if not self.enable_llm_repair or self.llm_repair is None:
                return ""
            context_html = await self._collect_repair_context(
                page,
                context_node=scrapling_node,
            )
            return await self.llm_repair.repair_selector(
                page=page,
                field_name=field_name,
                field_description=field_description,
                failed_selector=selector,
                context_html_or_screenshot=context_html,
            )

        result = await self.extraction_pipeline.extract(
            field,
            page_url=str(getattr(page, "url", "")),
            selector_extractor=extract_selector,
            adaptive_extractor=extract_adaptive if self.enable_adaptive else None,
            llm_candidate=generate_llm_candidate if self.enable_llm_repair else None,
            configured_observer=observe_configured,
            llm_trigger_confidence=float(
                self.config.get("repair_episode_confidence", 0.8)
            ),
        )
        if result.method == "scrapling_adaptive":
            logger.info("ADAPTIVE_SUCCESS field=%s, selector=%s", field_name, result.selector)
        elif result.method == "llm_text":
            logger.info(
                "LLM 已修复并提取字段: field=%s, page=%s, selector=%s",
                field_name,
                getattr(page, "url", ""),
                result.selector,
            )
        elif result.method == "exhausted":
            logger.debug("所有提取/修复层均失败：selector=%s, name=%s", selector, field_name)
        if self.experience_store is not None and (
            not result.validated
            or result.confidence < float(self.config.get("repair_episode_confidence", 0.8))
        ):
            await self._record_repair_episode(
                page,
                field,
                result,
                context_node=scrapling_node,
                capture_context=capture_context,
            )
        return result.value

    @staticmethod
    def _stable_hash(value: Any) -> str:
        encoded = json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        ).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()

    async def _record_repair_episode(
        self,
        page: Any,
        field: Dict[str, Any],
        result: Any,
        *,
        context_node: Optional[Any] = None,
        capture_context: Optional[Dict[str, Any]] = None,
        metadata_overrides: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Record a failed/low-confidence extraction in the opt-in v1 store."""
        html = await self._collect_repair_context(page, context_node=context_node)
        page_features_hash = hashlib.sha256(html.encode("utf-8")).hexdigest()
        extraction_plan = {
            "fields": [field],
            "captures": [
                {
                    "name": spec.name,
                    "type": spec.type,
                    "required": spec.required,
                    "max_bytes": spec.max_bytes,
                    **({"selector": spec.selector} if spec.selector else {}),
                    **({"url_glob": spec.url_glob} if spec.url_glob else {}),
                }
                for spec in self.capture_specs
            ],
            "request": {
                key: self.request_config.get(key)
                for key in ("wait_until", "wait_for_selector", "timeout_ms")
                if key in self.request_config
            },
        }
        validation = self.quality_gate.validate(result.value, field.get("validation"))
        page_url = str(getattr(page, "url", ""))
        authorization_category = str(
            self.config.get("authorization_category", "unknown") or "unknown"
        )
        is_failure = not result.validated
        attempts = list(getattr(result, "attempts", []) or [])
        llm_attempts = [
            attempt
            for attempt in attempts
            if isinstance(attempt, dict) and attempt.get("stage") == "llm_text"
        ]
        actual_llm_provider = (
            getattr(self.llm_repair, "provider", None)
            or self.llm_config.get("provider")
        )
        actual_llm_model = (
            getattr(self.llm_repair, "model", None)
            or self.llm_config.get("model")
        )
        proposal_summaries = [
            {
                "selector": self._readable_path(attempt.get("selector")),
                "accepted": bool(attempt.get("accepted")),
                "quality_gate": attempt.get("quality_gate", {}),
                "model": actual_llm_model,
                "provider": actual_llm_provider,
                "prompt_version": self.llm_config.get("prompt_version", "selector-repair-v1"),
                "input_summary": {
                    "field": self._readable_path(field.get("name")),
                    "page_features_sha256": page_features_hash,
                },
            }
            for attempt in llm_attempts
            if self._readable_path(attempt.get("selector"))
        ]
        metadata = {
            "repair_episode_schema": "RepairEpisode-v1",
            "target": self.name,
            "page_version": page_features_hash,
            "extraction_plan_sha256": self._stable_hash(extraction_plan),
            "page_features_sha256": page_features_hash,
            "failed_fields": [self._readable_path(field.get("name"))] if is_failure else [],
            "low_confidence_fields": (
                [self._readable_path(field.get("name"))] if not is_failure else []
            ),
            "failure_stage": result.method,
            "quality_gate": {
                "passed": validation.passed,
                "score": validation.score,
                "failed_rules": validation.failed_rules,
            },
            "pipeline_attempts": attempts,
            "proposals": proposal_summaries,
            "model": actual_llm_model if llm_attempts else None,
            "provider": actual_llm_provider if llm_attempts else None,
            "prompt_version": (
                self.llm_config.get("prompt_version", "selector-repair-v1")
                if llm_attempts
                else None
            ),
            "model_input_summary": (
                {"field": self._readable_path(field.get("name")), "page_features_sha256": page_features_hash}
                if llm_attempts
                else None
            ),
            "replay_metrics": {},
            "review": None,
            "human_decision": "pending",
            "artifact_hashes": [],
            "source_authorization_category": authorization_category,
        }
        if metadata_overrides:
            metadata.update(metadata_overrides)
        episode = self.experience_store.create_episode(
            authorization_category=authorization_category,
            source_url=page_url,
            page_pattern=page_url,
            retain_full_content=self.retain_full_episode_content,
            metadata=metadata,
        )
        capture = self.experience_store.add_capture(
            episode,
            "page_features",
            html,
            media_type="text/html",
            metadata={"page_features_sha256": page_features_hash},
        )
        artifact_hashes = [capture.artifact_sha256]

        declared_context = capture_context or {}
        for spec in self.capture_specs:
            if spec.name not in declared_context:
                continue
            capture_json = json.dumps(
                declared_context[spec.name],
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
            capture_event = self.experience_store.add_capture(
                episode,
                f"capture:{spec.name}",
                capture_json,
                media_type="application/json",
                metadata={"name": spec.name, "type": spec.type},
            )
            artifact_hashes.append(capture_event.artifact_sha256)

        for attempt in llm_attempts:
            candidate_selector = self._readable_path(attempt.get("selector"))
            if not candidate_selector:
                continue
            proposal = self.experience_store.add_proposal(
                episode,
                {
                    "fields": [
                        {
                            "name": self._readable_path(field.get("name")),
                            "selector": candidate_selector,
                        }
                    ]
                },
                source="llm_selector_candidate",
                rationale=(
                    "Candidate generated for the current explicit LLM-enabled run; "
                    "it remains pending human approval."
                ),
            )
            quality = attempt.get("quality_gate", {})
            self.experience_store.add_validation(
                episode,
                proposal_id=proposal,
                passed=bool(attempt.get("accepted")),
                validator="QualityGate",
                checks=quality,
                metrics={
                    "stage": attempt.get("stage"),
                    "confidence": attempt.get("confidence"),
                    "had_value": attempt.get("had_value"),
                },
            )
        self.experience_store.append_event(
            episode,
            "extraction_observation",
            {
                "field": self._readable_path(field.get("name")),
                "stage": result.method,
                "confidence": result.confidence,
                "validated": result.validated,
                "artifact_event_id": capture.id,
                "artifact_sha256": capture.artifact_sha256,
            },
            artifact_sha256=capture.artifact_sha256,
        )
        self.experience_store.append_event(
            episode,
            "artifact_manifest",
            {"artifact_hashes": sorted(set(artifact_hashes))},
        )
        self.repair_episode_ids.append(episode.id)

    async def _record_capture_failure_episode(
        self,
        page: Any,
        capture_session: PageCaptureSession,
        error: RequiredCaptureError,
    ) -> None:
        """Record a required-capture failure, then let the caller fail closed."""

        failed_specs = [
            spec
            for spec in capture_session.specs
            if spec.required and spec.name not in capture_session.values
        ]
        failed_names = [spec.name for spec in failed_specs]
        capture_errors = {
            spec.name: capture_session.errors.get(spec.name, "no matching capture")
            for spec in failed_specs
        }
        attempts = [
            {
                "stage": "capture",
                "selector": spec.selector or spec.url_glob,
                "capture": spec.name,
                "capture_type": spec.type,
                "confidence": 0.0,
                "accepted": False,
                "had_value": False,
                "quality_gate": {
                    "passed": False,
                    "score": 0.0,
                    "failed_rules": [capture_errors[spec.name]],
                },
                "reason": capture_errors[spec.name],
            }
            for spec in failed_specs
        ]
        label = ",".join(failed_names) or "required_capture"
        result = HealingResult(
            selector=label,
            value="",
            confidence=0.0,
            method="capture",
            validated=False,
            attempts=attempts,
        )
        await self._record_repair_episode(
            page,
            {
                "name": f"captures:{label}",
                "source": label,
                "validation": {"non_empty": {}},
            },
            result,
            capture_context=dict(capture_session.values),
            metadata_overrides={
                "failed_fields": [],
                "failed_captures": failed_names,
                "capture_errors": capture_errors,
                "capture_failure": str(error),
            },
        )

    async def _record_wait_failure_episode(
        self,
        page: Any,
        error: BaseException,
        *,
        capture_session: Optional[PageCaptureSession] = None,
    ) -> None:
        wait_plan = {
            key: self.request_config.get(key)
            for key in ("wait_until", "wait_for_selector", "timeout_ms")
            if key in self.request_config
        }
        result = HealingResult(
            selector=self._readable_path(wait_plan.get("wait_for_selector")),
            value="",
            confidence=0.0,
            method="wait",
            validated=False,
            attempts=[
                {
                    "stage": "wait",
                    "selector": self._readable_path(wait_plan.get("wait_for_selector")),
                    "confidence": 0.0,
                    "accepted": False,
                    "had_value": False,
                    "quality_gate": {
                        "passed": False,
                        "score": 0.0,
                        "failed_rules": [str(error) or type(error).__name__],
                    },
                    "reason": str(error) or type(error).__name__,
                }
            ],
        )
        await self._record_repair_episode(
            page,
            {
                "name": "page_wait",
                "selector": self._readable_path(wait_plan.get("wait_for_selector")),
                "validation": {"non_empty": {}},
            },
            result,
            capture_context=(
                dict(capture_session.values) if capture_session is not None else None
            ),
            metadata_overrides={
                "failed_fields": [],
                "failed_wait_conditions": wait_plan,
                "wait_error": str(error) or type(error).__name__,
                "capture_errors": (
                    dict(capture_session.errors) if capture_session is not None else {}
                ),
            },
        )

    async def _prepare_page_with_episode(
        self,
        page: Any,
        *,
        capture_session: Optional[PageCaptureSession] = None,
    ) -> None:
        try:
            await self._prepare_page(page)
        except (PlaywrightTimeoutError, asyncio.TimeoutError) as exc:
            if self.experience_store is not None:
                await self._record_wait_failure_episode(
                    page,
                    exc,
                    capture_session=capture_session,
                )
            raise

    def _has_repair_path(self, field: Dict[str, Any]) -> bool:
        if self._readable_path(field.get("selector")):
            return True
        raw_fallbacks = field.get("fallback_selectors", []) or []
        if isinstance(raw_fallbacks, (list, tuple)) and any(
            isinstance(value, str) and value.strip() for value in raw_fallbacks
        ):
            return True
        return bool(self.repair_memory.enabled or self.enable_llm_repair)

    async def _extract_fields(
        self,
        page,
        context: Dict[str, Any],
        *,
        capture_errors: Optional[Dict[str, str]] = None,
    ) -> List[Dict[str, Any]]:
        item_selector = self.config.get("item_selector") or self.config.get("list_item_selector")
        field_values: List[Dict[str, Any]] = []
        declared_capture_names = {spec.name for spec in self.capture_specs}

        if not self.fields:
            return field_values

        item_nodes = (
            await page.query_selector_all(item_selector)
            if item_selector
            else [None]
        )
        if not item_nodes:
            if self.experience_store is not None:
                failed_fields = [
                    self._readable_path(field.get("name"))
                    for field in self.fields
                    if self._readable_path(field.get("name"))
                ]
                result = HealingResult(
                    selector=self._readable_path(item_selector),
                    value="",
                    confidence=0.0,
                    method="item_selector",
                    validated=False,
                    attempts=[
                        {
                            "stage": "item_selector",
                            "selector": self._readable_path(item_selector),
                            "confidence": 0.0,
                            "accepted": False,
                            "had_value": False,
                            "quality_gate": {
                                "passed": False,
                                "score": 0.0,
                                "failed_rules": ["no_items"],
                            },
                        }
                    ],
                )
                await self._record_repair_episode(
                    page,
                    {
                        "name": "item_selector",
                        "selector": self._readable_path(item_selector),
                        "validation": {"non_empty": {}},
                    },
                    result,
                    capture_context=context,
                    metadata_overrides={
                        "failed_fields": failed_fields,
                        "failure_stage": "item_selector",
                        "item_selector": self._readable_path(item_selector),
                        "extraction_plan_sha256": self._stable_hash(
                            {
                                "item_selector": self._readable_path(item_selector),
                                "fields": self.fields,
                            }
                        ),
                    },
                )
            return field_values

        for node in item_nodes:
            record: Dict[str, Any] = {}
            for field in self.fields:
                name = self._readable_path(field.get("name"))
                if not name:
                    continue

                source = self._readable_path(field.get("source"))
                if source:
                    value = self._read_from_context(context, source)
                    source_validation = self.quality_gate.validate(
                        value,
                        field.get("validation"),
                    )
                    if value not in (None, "") and source_validation.passed:
                        record[name] = value
                        continue

                    if self.experience_store is not None:
                        source_result = HealingResult(
                            selector=source,
                            value=value,
                            confidence=0.0,
                            method="source",
                            validated=False,
                            attempts=[
                                {
                                    "stage": "source",
                                    "selector": source,
                                    "confidence": 0.0,
                                    "accepted": False,
                                    "had_value": value not in (None, ""),
                                    "quality_gate": {
                                        "passed": source_validation.passed,
                                        "score": source_validation.score,
                                        "failed_rules": source_validation.failed_rules,
                                    },
                                }
                            ],
                        )
                        target = node if node is not None and field.get("scope") != "page" else page
                        await self._record_repair_episode(
                            page,
                            field,
                            source_result,
                            context_node=target,
                            capture_context=context,
                            metadata_overrides=(
                                {"capture_errors": dict(capture_errors)}
                                if capture_errors
                                else None
                            ),
                        )

                    # Preserve pre-v2.1 source precedence for legacy action or
                    # payload fields. Only a source rooted in a declared JS
                    # capture may fall through to a selector after QualityGate
                    # rejects it.
                    if source.split(".", 1)[0] not in declared_capture_names:
                        record[name] = value
                        continue

                if not self._has_repair_path(field):
                    if source:
                        record[name] = ""
                    continue

                target = node if node is not None and field.get("scope") != "page" else page
                value = await self._extract_field_adaptive(
                    page,
                    field,
                    context_node=target,
                    capture_context=context,
                )
                record[name] = value

            if record:
                field_values.append(record)
        return field_values

    def _new_capture_session(self, page: Any) -> Optional[PageCaptureSession]:
        if not self.capture_specs:
            return None
        return PageCaptureSession(
            page,
            self.capture_specs,
            timeout_ms=int(self.request_config.get("timeout_ms", 30000)),
        )

    async def _scrape_current_page(
        self,
        page,
        url: Optional[str] = None,
        *,
        capture_session: Optional[PageCaptureSession] = None,
    ) -> List[Dict[str, Any]]:
        capture_session = capture_session or self._new_capture_session(page)
        if capture_session is not None:
            capture_session.install()
        try:
            if url:
                try:
                    await self._goto_page(page, url)
                except (PlaywrightTimeoutError, asyncio.TimeoutError) as exc:
                    if self.experience_store is not None:
                        await self._record_wait_failure_episode(
                            page,
                            exc,
                            capture_session=capture_session,
                        )
                    raise
            await self._prepare_page_with_episode(
                page,
                capture_session=capture_session,
            )

            action_context: Dict[str, Any] = {"page_url": page.url}
            extracted_records: List[Dict[str, Any]] = []

            for action in self.actions:
                if action.get("type") == "evaluate":
                    script = action.get("script")
                    if not isinstance(script, str) or not script.strip():
                        continue
                    result = await page.evaluate(script)
                    result_key = action.get("result_key")
                    if result_key:
                        action_context[result_key] = result
                    if action.get("as_records", False):
                        extracted_records.extend(self._ensure_records(result))

            if capture_session is not None:
                try:
                    action_context.update(await capture_session.finish())
                except RequiredCaptureError as exc:
                    if self.experience_store is not None:
                        await self._record_capture_failure_episode(
                            page,
                            capture_session,
                            exc,
                        )
                    raise

            if not extracted_records:
                extracted_records.extend(
                    await self._extract_fields(
                        page,
                        action_context,
                        capture_errors=(
                            dict(capture_session.errors)
                            if capture_session is not None
                            else None
                        ),
                    )
                )
        finally:
            if capture_session is not None:
                await capture_session.aclose()

        for row in extracted_records:
            if isinstance(row, dict):
                row.setdefault("crawl_time", datetime.now().strftime("%Y-%m-%d %H:%M:%S"))

        return extracted_records

    async def _crawl_pages(self, page, start_url: str) -> List[Dict[str, Any]]:
        records: List[Dict[str, Any]] = []
        page_index = 1
        capture_session: Optional[PageCaptureSession] = None

        self.scrapling_cache[id(page)] = {}

        while True:
            current = start_url if page_index == 1 else None
            records.extend(
                await self._scrape_current_page(
                    page,
                    current,
                    capture_session=capture_session,
                )
            )
            capture_session = None

            if not self.pagination.get("enabled"):
                break

            if page_index >= self.max_pages:
                break

            next_selector = self._readable_path(self.pagination.get("next_selector"))
            if not next_selector:
                break

            next_button = await page.query_selector(next_selector)
            if next_button is None:
                break

            is_disabled = await next_button.get_attribute("aria-disabled")
            if is_disabled and is_disabled.lower() == "true":
                break

            next_capture_session = self._new_capture_session(page)
            if next_capture_session is not None:
                next_capture_session.install()
            try:
                click_kwargs = self._select_kwargs(
                    next_button.click,
                    {
                        "timeout": int(self.request_config.get("timeout_ms", 30000)),
                    },
                )
                try:
                    await next_button.click(**click_kwargs)
                except (PlaywrightTimeoutError, asyncio.TimeoutError) as exc:
                    if self.experience_store is not None:
                        await self._record_wait_failure_episode(
                            page,
                            exc,
                            capture_session=next_capture_session,
                        )
                    raise
                await self._prepare_page_with_episode(
                    page,
                    capture_session=next_capture_session,
                )
                if delay := int(self.pagination.get("delay_ms", 0) or 0):
                    await page.wait_for_timeout(delay)
            except BaseException:
                if next_capture_session is not None:
                    await next_capture_session.aclose()
                raise
            # A Playwright Page object survives navigation, but its Scrapling
            # parser must not: cached HTML from page N can never be evidence for
            # page N+1.
            self.scrapling_cache[id(page)] = {}
            capture_session = next_capture_session
            page_index += 1

        return records

    async def run(self) -> List[Dict[str, Any]]:
        if not self.start_urls:
            raise RuntimeError("No start URL configured. Add start_url or start_urls in config.")

        browser = None
        context = None
        playwright_manager = async_playwright()
        try:
            p = await playwright_manager.__aenter__()
            try:
                cdp_url = self.browser_config.get("cdp_url")
                if cdp_url:
                    browser = await p.chromium.connect_over_cdp(cdp_url)
                else:
                    launch_kwargs = {
                        "headless": bool(self.browser_config.get("headless", False)),
                    }
                    if args := self.browser_config.get("launch"):
                        launch_kwargs.update({k: v for k, v in args.items() if v is not None})
                    if self.network_policy is not None:
                        policy_launch = dict(getattr(self.network_policy, "launch_options", {}))
                        policy_args = list(policy_launch.pop("args", []))
                        launch_args = list(launch_kwargs.get("args", []))
                        launch_kwargs["args"] = [
                            *launch_args,
                            *(arg for arg in policy_args if arg not in launch_args),
                        ]
                        launch_kwargs.update(policy_launch)
                    browser = await p.chromium.launch(**launch_kwargs)

                if browser.contexts:
                    context = browser.contexts[0]
                else:
                    context_kwargs = dict(self.browser_config.get("context", {}))
                    if self.network_policy is not None:
                        context_kwargs.update(self.network_policy.context_options)
                    context = await browser.new_context(**context_kwargs)

                if self.network_policy is not None:
                    await self.network_policy.install(context)

                if self.browser_config.get("stealth", False):
                    logger.warning(
                        "browser.stealth is retained as a compatible no-op; "
                        "the crawler does not provide detection-evasion behavior."
                    )

                for start_url in self.start_urls:
                    page = await context.new_page()
                    try:
                        page_records = await self._crawl_pages(page, start_url)
                        self.results.extend(page_records)
                    finally:
                        await _close_browser_resource(page, "browser page")
            finally:
                if context is not None:
                    await _close_browser_resource(context, "browser context")
                if browser is not None:
                    await _close_browser_resource(browser, "browser")
                self._close_scrapling_storages()
        finally:
            await _close_playwright_manager(playwright_manager)

        if self._adapter_post_process is not None:
            processed = self._adapter_post_process(self.results)
            if not isinstance(processed, list):
                raise TypeError("Adapter post_process() must return a list of records.")
            self.results = processed
        return self.results

    def save_json(self, records: List[Dict[str, Any]], out_file: Path) -> None:
        out_file.parent.mkdir(parents=True, exist_ok=True)
        out_file.write_text(json.dumps(records, ensure_ascii=False, indent=2), encoding="utf-8")

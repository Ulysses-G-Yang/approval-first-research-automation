from __future__ import annotations

import asyncio
import json
import logging
import inspect
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from playwright.async_api import async_playwright

from core.llm_repair import LLMRepair


logger = logging.getLogger(__name__)
_BROWSER_CLEANUP_TIMEOUT_SECONDS = 2.0


def _consume_close_outcome(task: "asyncio.Task[Any]") -> None:
    """Retrieve a detached cleanup result so it cannot emit an unhandled error."""
    try:
        task.result()
    except BaseException:
        pass


async def _close_browser_resource(resource: Any, label: str) -> None:
    """Attempt browser cleanup without allowing a stuck close to hang the run."""
    close_task = asyncio.create_task(resource.close())
    try:
        done, _ = await asyncio.wait(
            {close_task},
            timeout=_BROWSER_CLEANUP_TIMEOUT_SECONDS,
        )
    except asyncio.CancelledError:
        close_task.cancel()
        close_task.add_done_callback(_consume_close_outcome)
        raise
    if not done:
        close_task.cancel()
        close_task.add_done_callback(_consume_close_outcome)
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

try:
    from scrapling.parser import Selector as ScraplingSelector
except Exception:  # pragma: no cover
    try:
        from scrapling import Selector as ScraplingSelector
    except Exception:  # pragma: no cover
        ScraplingSelector = None  # type: ignore[assignment]

try:
    from playwright_stealth import Stealth
except Exception:  # pragma: no cover
    Stealth = None  # type: ignore[assignment]

try:
    from playwright_stealth import stealth_async
except Exception:  # pragma: no cover
    stealth_async = None


class GenericSpider:
    def __init__(self, config: Dict[str, Any], network_policy: Any = None):
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
        self.payload_key = self.config.get("payload_key", "payload")
        self.enable_adaptive = bool(self.config.get("enable_adaptive", True))
        self.llm_config = self.config.get("llm", {})
        self.enable_llm_repair = bool(self.llm_config.get("enable_repair", False))
        self.llm_repair = LLMRepair(self.llm_config) if self.enable_llm_repair else None
        self.scrapling_cache: Dict[int, Dict[str, Any]] = {}
        self.max_pages = int(self.pagination.get("max_pages", 1))
        self.results: List[Dict[str, Any]] = []

    def _collect_start_urls(self) -> List[str]:
        urls: List[str] = []
        start_url = self.config.get("start_url")
        if isinstance(start_url, str) and start_url.strip():
            urls.append(start_url.strip())
        for value in self.config.get("start_urls", []) or []:
            if isinstance(value, str) and value.strip():
                urls.append(value.strip())
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

        try:
            selector_obj = ScraplingSelector(
                html,
                adaptive=self.enable_adaptive,
                url=getattr(page, "url", "default"),
            )
        except TypeError:
            try:
                selector_obj = ScraplingSelector(html, adaptive=True, url=getattr(page, "url", "default"))
            except Exception as exc:  # pragma: no cover
                logger.warning("Scrapling 选择器初始化失败: %s", exc)
                return None
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
    ) -> str:
        selector = self._readable_path(field.get("selector"))
        if not selector:
            return ""

        attr = self._readable_path(field.get("attr"))
        field_name = self._readable_path(field.get("name"))
        field_description = self._readable_path(field.get("description")) or field_name

        value = await self._extract_playwright_field(context_node or page, selector, attr)
        if value:
            # Preserve a successful selector as Scrapling's baseline so a later
            # page revision has an adaptive reference instead of starting cold.
            if self.enable_adaptive:
                await self._extract_from_scrapling(
                    page=page,
                    selector=selector,
                    attr=attr,
                    node=context_node,
                    identifier=field_name or selector,
                )
            return value

        value = ""
        if self.enable_adaptive:
            value = await self._extract_from_scrapling(
                page=page,
                selector=selector,
                attr=attr,
                node=context_node,
                identifier=field_name or selector,
            )
            if value:
                logger.info(
                    "ADAPTIVE_SUCCESS field=%s, selector=%s",
                    field_name,
                    selector,
                )
                return value

        if not self.enable_llm_repair or self.llm_repair is None:
            if not value:
                logger.debug("adaptive/LLM 全部未启用或均失败：selector=%s, name=%s", selector, field_name)
            return value

        context_html = await self._collect_repair_context(page, context_node=context_node)
        repaired_selector = await self.llm_repair.repair_selector(
            page=page,
            field_name=field_name,
            field_description=field_description,
            failed_selector=selector,
            context_html_or_screenshot=context_html,
        )
        if not repaired_selector or repaired_selector == selector:
            logger.debug("LLM 未返回有效替代 selector：field=%s, original=%s", field_name, selector)
            return ""

        repaired_value = await self._extract_playwright_field(context_node or page, repaired_selector, attr)
        if repaired_value:
            logger.info(
                "LLM 已修复并提取字段: field=%s, page=%s, selector=%s -> %s",
                field_name,
                getattr(page, "url", ""),
                repaired_selector,
                repaired_value,
            )
            return repaired_value

        logger.warning("LLM修复后仍未提取到内容: field=%s, selector=%s", field_name, repaired_selector)
        return ""

    async def _extract_fields(self, page, context: Dict[str, Any]) -> List[Dict[str, Any]]:
        item_selector = self.config.get("item_selector") or self.config.get("list_item_selector")
        field_values: List[Dict[str, Any]] = []

        if not self.fields:
            return field_values

        item_nodes = (
            await page.query_selector_all(item_selector)
            if item_selector
            else [None]
        )
        if not item_nodes:
            return field_values

        for node in item_nodes:
            record: Dict[str, Any] = {}
            for field in self.fields:
                name = self._readable_path(field.get("name"))
                if not name:
                    continue

                if source := self._readable_path(field.get("source")):
                    value = self._read_from_context(context, source)
                    record[name] = value
                    continue

                selector = self._readable_path(field.get("selector"))
                if not selector:
                    continue

                target = node if node is not None and field.get("scope") != "page" else page
                value = await self._extract_field_adaptive(page, field, context_node=target)
                record[name] = value

            if record:
                field_values.append(record)
        return field_values

    async def _scrape_current_page(self, page, url: Optional[str] = None) -> List[Dict[str, Any]]:
        if url:
            await page.goto(url)
        await self._prepare_page(page)

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

        if not extracted_records:
            extracted_records.extend(await self._extract_fields(page, action_context))

        for row in extracted_records:
            if isinstance(row, dict):
                row.setdefault("crawl_time", datetime.now().strftime("%Y-%m-%d %H:%M:%S"))

        return extracted_records

    async def _crawl_pages(self, page, start_url: str) -> List[Dict[str, Any]]:
        records: List[Dict[str, Any]] = []
        page_index = 1

        self.scrapling_cache[id(page)] = {}

        while True:
            current = start_url if page_index == 1 else None
            records.extend(await self._scrape_current_page(page, current))

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

            await next_button.click()
            await self._prepare_page(page)
            if delay := int(self.pagination.get("delay_ms", 0) or 0):
                await page.wait_for_timeout(delay)
            page_index += 1

        return records

    async def _apply_context_stealth(self, context: Any) -> bool:
        """Apply the current playwright-stealth API before pages are created."""
        if not self.browser_config.get("stealth", False):
            return True

        if Stealth is not None:
            try:
                await Stealth(init_scripts_only=True).apply_stealth_async(context)
                return True
            except Exception as exc:  # pragma: no cover
                logger.warning("playwright-stealth 初始化失败，将尝试兼容模式: %s", exc)

        if stealth_async is not None:
            return False

        logger.warning(
            "配置要求 stealth=true，但 playwright-stealth 不可用；将继续运行但不会应用兼容层。"
        )
        return True

    async def run(self) -> List[Dict[str, Any]]:
        if not self.start_urls:
            raise RuntimeError("No start URL configured. Add start_url or start_urls in config.")

        browser = None
        context = None
        async with async_playwright() as p:
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

                context_stealth_ready = await self._apply_context_stealth(context)

                for start_url in self.start_urls:
                    page = await context.new_page()
                    try:
                        if (
                            self.browser_config.get("stealth", False)
                            and not context_stealth_ready
                            and stealth_async is not None
                        ):
                            try:
                                await stealth_async(page)
                            except Exception as exc:  # pragma: no cover
                                logger.warning("playwright-stealth 兼容模式应用失败: %s", exc)
                        page_records = await self._crawl_pages(page, start_url)
                        self.results.extend(page_records)
                    finally:
                        await _close_browser_resource(page, "browser page")
            finally:
                if context is not None:
                    await _close_browser_resource(context, "browser context")
                if browser is not None:
                    await _close_browser_resource(browser, "browser")

        return self.results

    def save_json(self, records: List[Dict[str, Any]], out_file: Path) -> None:
        out_file.parent.mkdir(parents=True, exist_ok=True)
        out_file.write_text(json.dumps(records, ensure_ascii=False, indent=2), encoding="utf-8")

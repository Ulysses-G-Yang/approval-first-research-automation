"""Compatibility facade for the unified field extraction pipeline.

``SelfHealingEngine`` keeps the public API used by earlier releases, but it no
longer owns a second selector, Scrapling, or Ollama implementation.  Browser
extraction, adaptive lookup, and LLM candidate generation are delegated to one
internal :class:`core.spider_engine.GenericSpider`; ordering and validation are
owned by the same :class:`core.extraction_pipeline.FieldExtractionPipeline`
used by normal crawler runs.
"""

from __future__ import annotations

import logging
from typing import Any, Optional

from core.extraction_pipeline import FieldExtractionPipeline, HealingResult
from core.llm_repair import LLMRepair
from core.spider_engine import GenericSpider


logger = logging.getLogger(__name__)


def _literal_flag(value: Any, label: str) -> bool:
    if not isinstance(value, bool):
        raise TypeError(f"{label} must be true or false.")
    return value


class SelfHealingEngine:
    """Backward-compatible facade over ``GenericSpider`` field extraction.

    Args:
        enable_llm: Enable the existing :class:`core.llm_repair.LLMRepair`
            candidate source. It remains disabled by default.
        llm_model: Local Ollama model forwarded to ``LLMRepair``. This preserves
            the earlier facade's local ``qwen3`` behavior without duplicating
            selector or pipeline logic.
        enable_scrapling: Enable GenericSpider's Scrapling adaptive callback.
        repair_db_path: Optional approved-repair JSONL path. ``None`` keeps
            repair memory disabled and creates no user file or directory.
    """

    def __init__(
        self,
        *,
        enable_llm: bool = False,
        llm_model: str = "qwen3",
        enable_scrapling: bool = True,
        repair_db_path: Optional[str] = None,
    ) -> None:
        self.enable_llm = _literal_flag(enable_llm, "enable_llm")
        self.llm_model = str(llm_model or "qwen3")
        self.enable_scrapling = _literal_flag(enable_scrapling, "enable_scrapling")

        repair_enabled = bool(repair_db_path)
        spider_config = {
            "enable_adaptive": self.enable_scrapling,
            "llm": {
                "enable_repair": self.enable_llm,
                "provider": "ollama",
                "model": self.llm_model,
                "timeout": 15,
            },
            "repair_memory": {
                "enabled": repair_enabled,
                "path": repair_db_path if repair_enabled else None,
            },
        }
        self._spider = GenericSpider(spider_config)

        # Preserve the attributes callers used on the former standalone engine.
        self.quality_gate = self._spider.quality_gate
        self.repair_memory = self._spider.repair_memory
        self.extraction_pipeline: FieldExtractionPipeline = (
            self._spider.extraction_pipeline
        )
        self.llm_repair = self._spider.llm_repair

    async def extract_with_healing(
        self,
        page: Any,
        field: Any,
        *,
        context_node: Any = None,
    ) -> HealingResult:
        """Extract one field and return the legacy ``HealingResult`` shape."""

        field_name = _text(_get_attr(field, "name", ""))
        field_selector = _text(_get_attr(field, "selector", ""))
        field_attr = _text(_get_attr(field, "attr", ""))
        field_description = (
            _text(_get_attr(field, "description", "")) or field_name
        )

        # Keep mutable compatibility flags effective for callers that set them
        # between runs, while all concrete behavior remains owned by GenericSpider.
        enable_scrapling = _literal_flag(self.enable_scrapling, "enable_scrapling")
        enable_llm = _literal_flag(self.enable_llm, "enable_llm")
        self._spider.enable_adaptive = enable_scrapling
        self._spider.enable_llm_repair = enable_llm
        if enable_llm:
            repairer_was_injected = self.llm_repair is not self._spider.llm_repair
            model_changed = (
                self.llm_repair is not None
                and not repairer_was_injected
                and getattr(self.llm_repair, "model", self.llm_model) != self.llm_model
            )
            if self.llm_repair is None or model_changed:
                self.llm_repair = LLMRepair(
                    {
                        "enable_repair": True,
                        "provider": "ollama",
                        "model": self.llm_model,
                        "timeout": 15,
                    }
                )
            self._spider.llm_repair = self.llm_repair

        async def extract_selector(
            selector: str,
            attr: Optional[str],
        ) -> str:
            return await self._spider._extract_playwright_field(
                context_node or page,
                selector,
                attr or "",
            )

        async def observe_configured(selector: str) -> None:
            if not enable_scrapling:
                return
            await self._spider._extract_from_scrapling(
                page=page,
                selector=selector,
                attr=field_attr,
                node=context_node,
                identifier=field_name or selector,
            )

        async def extract_adaptive(
            selector: str,
            attr: Optional[str],
            identifier: str,
        ) -> str:
            return await self._spider._extract_from_scrapling(
                page=page,
                selector=selector,
                attr=attr or "",
                node=context_node,
                identifier=identifier or selector,
            )

        async def generate_llm_candidate() -> str:
            repairer = self.llm_repair
            if repairer is None:
                return ""
            context = await self._spider._collect_repair_context(
                page,
                context_node=context_node,
            )
            return await repairer.repair_selector(
                page=page,
                field_name=field_name,
                field_description=field_description,
                failed_selector=field_selector,
                context_html_or_screenshot=context,
            )

        result = await self.extraction_pipeline.extract(
            field,
            page_url=_page_url(page),
            selector_extractor=extract_selector,
            adaptive_extractor=(
                extract_adaptive if enable_scrapling else None
            ),
            llm_candidate=(
                generate_llm_candidate
                if enable_llm and self.llm_repair is not None
                else None
            ),
            configured_observer=(
                observe_configured if enable_scrapling else None
            ),
            llm_trigger_confidence=0.8,
        )
        if result.method == "exhausted":
            logger.warning(
                "All layers exhausted for field=%s selector=%s",
                field_name,
                field_selector,
            )
        return result


def _get_attr(value: Any, name: str, default: Any = None) -> Any:
    if isinstance(value, dict):
        return value.get(name, default)
    return getattr(value, name, default)


def _text(value: Any) -> str:
    return str(value).strip() if value is not None else ""


def _page_url(page: Any) -> str:
    try:
        return str(getattr(page, "url", ""))
    except Exception:
        return ""


__all__ = ["HealingResult", "SelfHealingEngine"]

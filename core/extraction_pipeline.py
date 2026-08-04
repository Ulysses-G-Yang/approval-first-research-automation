"""Shared field extraction and repair pipeline.

The pipeline is deliberately browser- and model-agnostic.  Callers provide the
exact selector, adaptive, and candidate-generation operations while this module
owns the ordering and applies one :class:`QualityGate` to every candidate.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Optional

from core.quality_gate import QualityGate
from core.repair_persistence import RepairPersistence


SelectorExtractor = Callable[[str, Optional[str]], Awaitable[str]]
AdaptiveExtractor = Callable[[str, Optional[str], str], Awaitable[str]]
CandidateGenerator = Callable[[], Awaitable[str]]
ConfiguredObserver = Callable[[str], Awaitable[None]]


@dataclass
class HealingResult:
    """Result of one field extraction through the shared pipeline."""

    selector: str
    value: str
    confidence: float
    method: str
    validated: bool
    attempts: list[dict[str, Any]] = field(default_factory=list)


def _field_value(field: Any, name: str, default: Any = None) -> Any:
    if isinstance(field, dict):
        return field.get(name, default)
    return getattr(field, name, default)


class FieldExtractionPipeline:
    """Run the one supported field extraction/repair order.

    The order is fixed: configured selector, fallback selectors, an explicitly
    approved historical repair, adaptive extraction, an optional LLM candidate,
    then an empty result.  Generated LLM candidates are retried and validated in
    the current run only; this class never persists or approves them.
    """

    def __init__(
        self,
        *,
        quality_gate: Optional[QualityGate] = None,
        repair_memory: Optional[RepairPersistence] = None,
    ) -> None:
        self.quality_gate = quality_gate or QualityGate()
        self.repair_memory = repair_memory or RepairPersistence()

    async def extract(
        self,
        field: Any,
        *,
        page_url: str,
        selector_extractor: SelectorExtractor,
        adaptive_extractor: Optional[AdaptiveExtractor] = None,
        llm_candidate: Optional[CandidateGenerator] = None,
        configured_observer: Optional[ConfiguredObserver] = None,
        llm_trigger_confidence: float = 0.8,
    ) -> HealingResult:
        field_name = str(_field_value(field, "name", "") or "").strip()
        configured_selector = str(_field_value(field, "selector", "") or "").strip()
        attr_value = _field_value(field, "attr", None)
        attr = str(attr_value).strip() if attr_value else None
        validation = _field_value(field, "validation", None)
        raw_fallbacks = _field_value(field, "fallback_selectors", []) or []
        if not isinstance(raw_fallbacks, (list, tuple)):
            raw_fallbacks = []
        fallback_selectors = [
            str(selector).strip()
            for selector in raw_fallbacks
            if isinstance(selector, str) and selector.strip()
        ]
        attempts: list[dict[str, Any]] = []

        def record_attempt(
            *,
            stage: str,
            selector: str,
            confidence: float,
            value: Any,
            validation_result: Any,
            accepted: bool,
            reason: str = "",
        ) -> None:
            attempts.append(
                {
                    "stage": stage,
                    "selector": selector,
                    "confidence": confidence,
                    "accepted": accepted,
                    "had_value": bool(str(value).strip()) if value is not None else False,
                    "quality_gate": {
                        "passed": bool(validation_result.passed),
                        "score": float(validation_result.score),
                        "failed_rules": list(validation_result.failed_rules),
                    },
                    **({"reason": reason} if reason else {}),
                }
            )

        async def try_selector(
            selector: str,
            *,
            method: str,
            confidence: float,
        ) -> Optional[HealingResult]:
            if not selector:
                return None
            value = await selector_extractor(selector, attr)
            validation_result = self.quality_gate.validate(value, validation)
            accepted = bool(value) and validation_result.passed
            record_attempt(
                stage=method,
                selector=selector,
                confidence=confidence,
                value=value,
                validation_result=validation_result,
                accepted=accepted,
            )
            if not accepted:
                return None
            return HealingResult(
                selector=selector,
                value=value,
                confidence=confidence,
                method=method,
                validated=True,
                attempts=list(attempts),
            )

        configured = await try_selector(
            configured_selector,
            method="configured",
            confidence=1.0,
        )
        if configured is not None:
            if configured_observer is not None:
                await configured_observer(configured_selector)
            return configured

        for selector in fallback_selectors:
            fallback = await try_selector(
                selector,
                method="fallback",
                confidence=0.9,
            )
            if fallback is not None:
                return fallback

        historical_selector = self.repair_memory.suggest(field_name, page_url)
        if historical_selector:
            historical = await try_selector(
                historical_selector,
                method="cached_repair",
                confidence=0.85,
            )
            if historical is not None:
                return historical

        if adaptive_extractor is not None and configured_selector:
            adaptive_value = await adaptive_extractor(
                configured_selector,
                attr,
                field_name,
            )
            adaptive_validation = self.quality_gate.validate(adaptive_value, validation)
            adaptive_accepted = bool(adaptive_value) and adaptive_validation.passed
            record_attempt(
                stage="scrapling_adaptive",
                selector=configured_selector,
                confidence=0.7,
                value=adaptive_value,
                validation_result=adaptive_validation,
                accepted=adaptive_accepted,
            )
            adaptive_result = None
            if adaptive_accepted:
                adaptive_result = HealingResult(
                    selector=configured_selector,
                    value=adaptive_value,
                    confidence=0.7,
                    method="scrapling_adaptive",
                    validated=True,
                    attempts=list(attempts),
                )
                if llm_candidate is None or adaptive_result.confidence >= llm_trigger_confidence:
                    return adaptive_result
        else:
            adaptive_result = None

        if llm_candidate is not None:
            candidate = str(await llm_candidate() or "").strip()
            if candidate and candidate != configured_selector:
                repaired = await try_selector(
                    candidate,
                    method="llm_text",
                    confidence=0.75,
                )
                if repaired is not None:
                    return repaired
            elif candidate:
                duplicate_validation = self.quality_gate.validate("", validation)
                record_attempt(
                    stage="llm_text",
                    selector=candidate,
                    confidence=0.75,
                    value="",
                    validation_result=duplicate_validation,
                    accepted=False,
                    reason="candidate_matches_configured_selector",
                )

        if adaptive_result is not None:
            adaptive_result.attempts = list(attempts)
            return adaptive_result

        return HealingResult(
            selector=configured_selector,
            value="",
            confidence=0.0,
            method="exhausted",
            validated=False,
            attempts=list(attempts),
        )


__all__ = ["FieldExtractionPipeline", "HealingResult"]

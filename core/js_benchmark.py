"""Deterministic, offline benchmark for JavaScript-oriented page evolution.

The benchmark intentionally replays checked-in synthetic artifacts instead of
opening a browser or contacting a model provider. DOM cases exercise the shared
:class:`~core.extraction_pipeline.FieldExtractionPipeline`; capture cases use
the production :class:`~core.captures.PageCaptureSession`, and hydration cases
use :meth:`core.spider_engine.GenericSpider._prepare_page` against deterministic
Playwright-shaped fixture objects.

``run_benchmark`` is the stable synchronous entry point for the ``crawler``
CLI.  ``run_benchmark_async`` is available for test harnesses and callers that
already own an event loop.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import socket
import time
import uuid
from collections.abc import Mapping
from contextlib import contextmanager
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any, Iterator

from scrapling.parser import Selector

from core.captures import PageCaptureSession, parse_capture_specs
from core.extraction_pipeline import FieldExtractionPipeline
from core.experience_store import ExperienceStore
from core.llm_repair import LLMRepair
from core.quality_gate import QualityGate
from core.spider_engine import GenericSpider


SCHEMA_VERSION = "1.0"
DEFAULT_CORPUS_ID = "js-evolution-v1"
_EPISODE_NAMESPACE = uuid.UUID("e60ad9c5-170c-4f7d-93a7-e2e406ced343")


class BenchmarkConfigurationError(ValueError):
    """Raised when the local corpus is malformed or escapes its fixture root."""


class OfflineBoundaryError(RuntimeError):
    """Raised if benchmark code attempts to make a network connection."""


def _default_corpus_path() -> Path:
    return Path(__file__).resolve().parents[1] / "labs" / "js_evolution" / "corpus.json"


def _canonical_json(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _episode_id(corpus_id: str, case_id: str) -> str:
    return str(uuid.uuid5(_EPISODE_NAMESPACE, f"{corpus_id}:{case_id}"))


def _resolve_fixture(root: Path, relative: str) -> Path:
    if not relative or Path(relative).is_absolute():
        raise BenchmarkConfigurationError("Fixture paths must be non-empty and relative.")
    resolved_root = root.resolve()
    resolved = (resolved_root / relative).resolve()
    try:
        resolved.relative_to(resolved_root)
    except ValueError as exc:
        raise BenchmarkConfigurationError(
            f"Fixture path escapes the local corpus root: {relative}"
        ) from exc
    if not resolved.is_file():
        raise BenchmarkConfigurationError(f"Fixture does not exist: {relative}")
    return resolved


def _load_corpus(
    corpus_path: str | Path | None,
    fixture_root: str | Path | None,
) -> tuple[dict[str, Any], Path, str]:
    manifest_path = Path(corpus_path) if corpus_path is not None else _default_corpus_path()
    if manifest_path.is_dir():
        manifest_path = manifest_path / "corpus.json"
    manifest_path = manifest_path.resolve()
    if not manifest_path.is_file():
        raise BenchmarkConfigurationError(f"Benchmark corpus not found: {manifest_path}")

    try:
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise BenchmarkConfigurationError(f"Cannot read benchmark corpus: {exc}") from exc
    if not isinstance(payload, dict):
        raise BenchmarkConfigurationError("Benchmark corpus must be a JSON object.")
    if payload.get("schema_version") != SCHEMA_VERSION:
        raise BenchmarkConfigurationError(
            f"Unsupported benchmark schema: {payload.get('schema_version')!r}"
        )
    corpus_id = str(payload.get("corpus_id", "")).strip()
    if not corpus_id:
        raise BenchmarkConfigurationError("Benchmark corpus_id is required.")
    cases = payload.get("cases")
    if not isinstance(cases, list) or not cases:
        raise BenchmarkConfigurationError("Benchmark corpus cases must be a non-empty list.")
    ids = [str(case.get("id", "")) for case in cases if isinstance(case, dict)]
    if len(ids) != len(cases) or any(not case_id for case_id in ids) or len(set(ids)) != len(ids):
        raise BenchmarkConfigurationError("Every benchmark case needs a unique non-empty id.")

    root = Path(fixture_root).resolve() if fixture_root is not None else manifest_path.parent
    manifest_hash = _sha256(manifest_path.read_bytes())
    return payload, root, manifest_hash


@contextmanager
def _offline_guard(counters: dict[str, int]) -> Iterator[None]:
    """Block socket connection attempts while replaying the local corpus."""

    original_create_connection = socket.create_connection
    original_connect = socket.socket.connect
    original_model_call = LLMRepair.repair_selector

    def deny_connection(*_args: Any, **_kwargs: Any) -> Any:
        counters["external_network_calls"] += 1
        raise OfflineBoundaryError("JS benchmark forbids all network connections.")

    async def deny_model_call(*_args: Any, **_kwargs: Any) -> str:
        counters["real_model_calls"] += 1
        raise OfflineBoundaryError("JS benchmark forbids real model calls.")

    socket.create_connection = deny_connection  # type: ignore[assignment]
    socket.socket.connect = deny_connection  # type: ignore[method-assign]
    LLMRepair.repair_selector = deny_model_call  # type: ignore[method-assign]
    try:
        yield
    finally:
        socket.create_connection = original_create_connection
        socket.socket.connect = original_connect
        LLMRepair.repair_selector = original_model_call  # type: ignore[method-assign]


def _selector_text(document: Selector, selector: str, attr: str | None = None) -> str:
    expression = f"{selector}::attr({attr})" if attr else f"{selector}::text"
    values = document.css(expression).getall()
    return " ".join(str(item).strip() for item in values if str(item).strip()).strip()


def _case_fixture(case: Mapping[str, Any], root: Path) -> tuple[Path, bytes]:
    fixture = _resolve_fixture(root, str(case.get("fixture", "")))
    return fixture, fixture.read_bytes()


def _result_passed(case: Mapping[str, Any], result: Mapping[str, Any]) -> bool:
    category = case.get("category")
    expected_stage = case.get("expected_stage")
    stage_matches = result.get("pipeline_stage") == expected_stage
    if category in {"normal", "recoverable"}:
        return bool(
            stage_matches
            and result.get("actual_value") == case.get("expected_value")
            and result.get("quality_gate_passed") is True
        )
    if category == "invalid_candidate":
        return bool(
            stage_matches
            and result.get("accepted_invalid_candidate") is False
            and result.get("actual_value") == ""
        )
    if category == "irrecoverable":
        return bool(stage_matches and result.get("actual_value") == "")
    raise BenchmarkConfigurationError(f"Unknown case category: {category!r}")


async def _run_dom_case(
    case: Mapping[str, Any],
    root: Path,
    counters: dict[str, int],
) -> dict[str, Any]:
    fixture, fixture_bytes = _case_fixture(case, root)
    document = Selector(fixture_bytes.decode("utf-8"))
    field = case.get("field")
    if not isinstance(field, dict):
        raise BenchmarkConfigurationError(f"DOM case {case['id']} needs a field object.")
    trace: list[dict[str, Any]] = []

    async def selector_extractor(selector: str, attr: str | None) -> str:
        value = _selector_text(document, selector, attr)
        trace.append({"operation": "selector", "selector": selector, "value": value})
        return value

    candidate_selector = str(case.get("fixture_candidate", "")).strip()

    async def fixture_candidate() -> str:
        counters["fixture_candidate_calls"] += 1
        trace.append({"operation": "fixture_candidate", "selector": candidate_selector})
        return candidate_selector

    pipeline = FieldExtractionPipeline()
    healing = await pipeline.extract(
        field,
        page_url=f"fixture://{case['id']}",
        selector_extractor=selector_extractor,
        llm_candidate=fixture_candidate if candidate_selector else None,
    )

    pipeline_stage = healing.method
    accepted_invalid = bool(
        case.get("category") == "invalid_candidate"
        and candidate_selector
        and healing.method == "llm_text"
        and healing.selector == candidate_selector
    )
    if case.get("category") == "invalid_candidate" and candidate_selector:
        candidate_reextractions = [
            entry
            for entry in trace
            if entry.get("operation") == "selector"
            and entry.get("selector") == candidate_selector
        ]
        candidate_value = (
            str(candidate_reextractions[-1].get("value", ""))
            if candidate_reextractions
            else ""
        )
        if candidate_value and not accepted_invalid and healing.method == "exhausted":
            pipeline_stage = "quality_gate_rejected"

    result = {
        "actual_value": healing.value,
        "pipeline_stage": pipeline_stage,
        "terminal_stage": healing.method,
        "quality_gate_passed": healing.validated,
        "accepted_invalid_candidate": accepted_invalid,
        "trace": trace,
        "fixture_hash": _sha256(fixture_bytes),
    }
    result["passed"] = _result_passed(case, result)
    return result


class _FixtureTextElement:
    def __init__(self, value: str) -> None:
        self.value = value

    async def text_content(self) -> str:
        return self.value

    async def inner_text(self) -> str:
        return self.value

    async def get_attribute(self, _name: str) -> str:
        return ""


class _FixtureCapturePage:
    """Small event/page adapter used only to drive production capture code."""

    def __init__(self, html: str = "") -> None:
        self.document = Selector(html) if html else None
        self.listeners: dict[str, list[Any]] = {}

    def on(self, event: str, callback: Any) -> None:
        self.listeners.setdefault(event, []).append(callback)

    def remove_listener(self, event: str, callback: Any) -> None:
        callbacks = self.listeners.get(event, [])
        if callback in callbacks:
            callbacks.remove(callback)

    async def query_selector(self, selector: str) -> _FixtureTextElement | None:
        if self.document is None:
            return None
        try:
            result = self.document.css(selector)
        except Exception:
            return None
        if getattr(result, "first", None) is None:
            return None
        return _FixtureTextElement(_selector_text(self.document, selector))

    def emit_response(self, response: Any) -> None:
        for callback in tuple(self.listeners.get("response", [])):
            callback(response)


class _FixtureRequest:
    def __init__(self, method: str, resource_type: str) -> None:
        self.method = method
        self.resource_type = resource_type


class _FixtureResponse:
    def __init__(self, value: Mapping[str, Any]) -> None:
        self.url = str(value.get("url", ""))
        self.status = int(value.get("status", 0) or 0)
        self._raw_body = _canonical_json(value.get("body"))
        self.headers = {
            "content-type": str(value.get("content_type", "")),
            "content-length": str(len(self._raw_body)),
        }
        self.request = _FixtureRequest(
            str(value.get("method", "")),
            str(value.get("resource_type", "")),
        )

    async def body(self) -> bytes:
        return self._raw_body


def _capture_spec(
    capture: Mapping[str, Any],
    *,
    capture_type: str,
    name: str,
) -> tuple[Any, ...]:
    raw: dict[str, Any] = {
        "name": name,
        "type": capture_type,
        "required": False,
        "max_bytes": int(capture.get("max_bytes", 1_000_000) or 1_000_000),
    }
    key = "selector" if capture_type == "embedded_json" else "url_glob"
    raw[key] = str(capture.get(key, "")).strip()
    return parse_capture_specs([raw])


def _capture_value(
    values: Mapping[str, Any],
    *,
    name: str,
    source: str,
) -> str:
    reader = GenericSpider({"enable_adaptive": False})
    path = f"{name}.{source}" if source else name
    value = reader._read_from_context(dict(values), path)
    if value is None:
        return ""
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return str(value).strip()


async def _embedded_value(html: str, capture: Mapping[str, Any], *, name: str) -> str:
    page = _FixtureCapturePage(html)
    session = PageCaptureSession(
        page,
        _capture_spec(capture, capture_type="embedded_json", name=name),
    )
    session.install()
    try:
        values = await session.finish()
    finally:
        session.close()
    return _capture_value(
        values,
        name=name,
        source=str(capture.get("source", "")).strip(),
    )


async def _run_embedded_case(case: Mapping[str, Any], root: Path) -> dict[str, Any]:
    _fixture, fixture_bytes = _case_fixture(case, root)
    html = fixture_bytes.decode("utf-8")
    original = case.get("original_capture")
    repair = case.get("repair_capture")
    if not isinstance(original, dict) or not isinstance(repair, dict):
        raise BenchmarkConfigurationError("Embedded JSON cases need original_capture and repair_capture.")
    original_value = await _embedded_value(html, original, name="original")
    repair_value = (
        await _embedded_value(html, repair, name="repair")
        if not original_value
        else original_value
    )
    validation = case.get("validation")
    gate = QualityGate.validate(repair_value, validation if isinstance(validation, dict) else None)
    stage = "embedded_json_repair" if not original_value and repair_value else "embedded_json"
    result = {
        "actual_value": repair_value if gate.passed else "",
        "pipeline_stage": stage,
        "quality_gate_passed": gate.passed,
        "accepted_invalid_candidate": False,
        "trace": [
            {"operation": "embedded_json", "plan": "original", "value": original_value},
            {"operation": "embedded_json", "plan": "repair", "value": repair_value},
            {"operation": "quality_gate", "passed": gate.passed, "failed_rules": gate.failed_rules},
        ],
        "fixture_hash": _sha256(fixture_bytes),
    }
    result["passed"] = _result_passed(case, result)
    return result


async def _network_value(
    payload: Mapping[str, Any],
    capture: Mapping[str, Any],
    *,
    name: str,
) -> str:
    responses = payload.get("responses", [])
    if not isinstance(responses, list):
        return ""
    page = _FixtureCapturePage()
    session = PageCaptureSession(
        page,
        _capture_spec(capture, capture_type="network_json", name=name),
    )
    session.install()
    try:
        for response in responses:
            if isinstance(response, Mapping):
                page.emit_response(_FixtureResponse(response))
        values = await session.finish()
    finally:
        session.close()
    return _capture_value(
        values,
        name=name,
        source=str(capture.get("source", "")).strip(),
    )


async def _run_network_case(case: Mapping[str, Any], root: Path) -> dict[str, Any]:
    _fixture, fixture_bytes = _case_fixture(case, root)
    payload = json.loads(fixture_bytes.decode("utf-8"))
    if not isinstance(payload, dict):
        raise BenchmarkConfigurationError("Network replay fixture must be a JSON object.")
    original = case.get("original_capture")
    repair = case.get("repair_capture")
    if not isinstance(original, dict) or not isinstance(repair, dict):
        raise BenchmarkConfigurationError("Network JSON cases need original_capture and repair_capture.")
    original_value = await _network_value(payload, original, name="original")
    repair_value = (
        await _network_value(payload, repair, name="repair")
        if not original_value
        else original_value
    )
    validation = case.get("validation")
    gate = QualityGate.validate(repair_value, validation if isinstance(validation, dict) else None)
    stage = "network_json_repair" if not original_value and repair_value else "network_json"
    result = {
        "actual_value": repair_value if gate.passed else "",
        "pipeline_stage": stage,
        "quality_gate_passed": gate.passed,
        "accepted_invalid_candidate": False,
        "trace": [
            {"operation": "network_json", "plan": "original", "value": original_value},
            {"operation": "network_json", "plan": "repair", "value": repair_value},
            {"operation": "quality_gate", "passed": gate.passed, "failed_rules": gate.failed_rules},
        ],
        "fixture_hash": _sha256(fixture_bytes),
    }
    result["passed"] = _result_passed(case, result)
    return result


class _FixtureHydrationPage:
    """Deterministic wait model that is driven by GenericSpider._prepare_page."""

    url = "fixture://hydration"

    def __init__(self, payload: Mapping[str, Any]) -> None:
        snapshots = payload.get("snapshots", [])
        self.snapshots = [item for item in snapshots if isinstance(item, Mapping)] if isinstance(snapshots, list) else []
        self.visible: dict[str, Any] = {}

    async def wait_for_selector(self, selector: str, *, timeout: int) -> _FixtureTextElement:
        ordered = sorted(
            self.snapshots,
            key=lambda item: int(item.get("after_ms", 0) or 0),
        )
        for snapshot in ordered:
            after_ms = int(snapshot.get("after_ms", 0) or 0)
            selectors = snapshot.get("selectors", {})
            if after_ms <= timeout and isinstance(selectors, Mapping) and selector in selectors:
                self.visible = dict(selectors)
                return _FixtureTextElement(str(selectors[selector]))
        raise TimeoutError(f"selector {selector!r} was not visible within {timeout}ms")

    async def query_selector(self, selector: str) -> _FixtureTextElement | None:
        if selector not in self.visible:
            return None
        return _FixtureTextElement(str(self.visible[selector]))


async def _hydrated_value(
    payload: Mapping[str, Any],
    selector: str,
    timeout_ms: int,
) -> str:
    page = _FixtureHydrationPage(payload)
    spider = GenericSpider(
        {
            "enable_adaptive": False,
            "request": {
                "wait_for_selector": selector,
                "timeout_ms": timeout_ms,
            },
        }
    )
    try:
        await spider._prepare_page(page)
    except TimeoutError:
        return ""
    return await spider._extract_playwright_field(page, selector, "")


async def _run_hydration_case(case: Mapping[str, Any], root: Path) -> dict[str, Any]:
    _fixture, fixture_bytes = _case_fixture(case, root)
    payload = json.loads(fixture_bytes.decode("utf-8"))
    if not isinstance(payload, dict):
        raise BenchmarkConfigurationError("Hydration replay fixture must be a JSON object.")
    selector = str(case.get("selector", ""))
    original_value = await _hydrated_value(
        payload,
        selector,
        int(case.get("original_timeout_ms", 0) or 0),
    )
    repair_value = (
        await _hydrated_value(
            payload,
            selector,
            int(case.get("repair_timeout_ms", 0) or 0),
        )
        if not original_value
        else original_value
    )
    validation = case.get("validation")
    gate = QualityGate.validate(repair_value, validation if isinstance(validation, dict) else None)
    stage = "wait_condition_repair" if not original_value and repair_value else "configured"
    result = {
        "actual_value": repair_value if gate.passed else "",
        "pipeline_stage": stage,
        "quality_gate_passed": gate.passed,
        "accepted_invalid_candidate": False,
        "trace": [
            {"operation": "wait", "plan": "original", "value": original_value},
            {"operation": "wait", "plan": "repair", "value": repair_value},
            {"operation": "quality_gate", "passed": gate.passed, "failed_rules": gate.failed_rules},
        ],
        "fixture_hash": _sha256(fixture_bytes),
    }
    result["passed"] = _result_passed(case, result)
    return result


async def _execute_case(
    case: Mapping[str, Any],
    root: Path,
    corpus_id: str,
    counters: dict[str, int],
    episode_store: ExperienceStore,
) -> dict[str, Any]:
    started = time.perf_counter_ns()
    kind = case.get("kind")
    if kind == "dom":
        observed = await _run_dom_case(case, root, counters)
    elif kind == "embedded_json":
        observed = await _run_embedded_case(case, root)
    elif kind == "network_json":
        observed = await _run_network_case(case, root)
    elif kind == "hydration":
        observed = await _run_hydration_case(case, root)
    else:
        raise BenchmarkConfigurationError(f"Unknown benchmark case kind: {kind!r}")

    duration_ms = round((time.perf_counter_ns() - started) / 1_000_000, 3)
    repair_episode_id = _episode_id(corpus_id, str(case["id"]))
    deterministic = {
        "case_id": case["id"],
        "category": case["category"],
        "kind": kind,
        "expected_value": case.get("expected_value", ""),
        "actual_value": observed["actual_value"],
        "expected_stage": case["expected_stage"],
        "pipeline_stage": observed["pipeline_stage"],
        "terminal_stage": observed.get("terminal_stage", observed["pipeline_stage"]),
        "repair_episode_id": repair_episode_id,
        "quality_gate_passed": observed["quality_gate_passed"],
        "accepted_invalid_candidate": observed["accepted_invalid_candidate"],
        "fixture_hash": observed["fixture_hash"],
        "trace": observed["trace"],
        "passed": observed["passed"],
    }
    artifact_hash = _sha256(_canonical_json(deterministic))
    episode = episode_store.create_episode(
        authorization_category="synthetic_local",
        source_url=f"fixture://{case['id']}",
        page_pattern=f"fixture://{case['id']}",
        episode_id=repair_episode_id,
        status="benchmark",
        created_at="2000-01-01T00:00:00+00:00",
        metadata={
            "origin": "js_evolution_benchmark",
            "corpus_id": corpus_id,
            "case_id": case["id"],
            "pipeline_stage": observed["pipeline_stage"],
        },
    )
    episode_store.append_event(
        episode,
        "benchmark_replay",
        {
            "passed": observed["passed"],
            "quality_gate_passed": observed["quality_gate_passed"],
            "artifact_hash": artifact_hash,
        },
        created_at="2000-01-01T00:00:00+00:00",
    )
    return {**deterministic, "artifact_hash": artifact_hash, "duration_ms": duration_ms}


def _rate(cases: list[dict[str, Any]], category: str, predicate: Any) -> dict[str, Any]:
    selected = [case for case in cases if case["category"] == category]
    passed = sum(1 for case in selected if predicate(case))
    total = len(selected)
    return {"passed": passed, "total": total, "rate": passed / total if total else 0.0}


def _build_metrics(
    cases: list[dict[str, Any]],
    counters: Mapping[str, int],
    *,
    artifact_hashes_deterministic: bool,
    non_timing_metrics_deterministic: bool,
    elapsed_ms: float,
) -> dict[str, Any]:
    normal = _rate(cases, "normal", lambda case: case["actual_value"] == case["expected_value"])
    recoverable = _rate(cases, "recoverable", lambda case: case["passed"])
    invalid = [case for case in cases if case["category"] == "invalid_candidate"]
    invalid_accepted = sum(1 for case in invalid if case["accepted_invalid_candidate"])
    irrecoverable = _rate(
        cases,
        "irrecoverable",
        lambda case: case["actual_value"] == "" and case["passed"],
    )
    stage_matches = sum(1 for case in cases if case["pipeline_stage"] == case["expected_stage"])
    metrics = {
        "normal_exact_match": normal,
        "recoverable_recovery": recoverable,
        "invalid_candidate_acceptance": {
            "accepted": invalid_accepted,
            "total": len(invalid),
            "rate": invalid_accepted / len(invalid) if invalid else 0.0,
        },
        "irrecoverable_correct_failure": irrecoverable,
        "expected_stage_match": {
            "passed": stage_matches,
            "total": len(cases),
            "rate": stage_matches / len(cases) if cases else 0.0,
        },
        "external_network_calls": int(counters.get("external_network_calls", 0)),
        "real_model_calls": int(counters.get("real_model_calls", 0)),
        "fixture_candidate_calls": int(counters.get("fixture_candidate_calls", 0)),
        "artifact_hashes_deterministic": artifact_hashes_deterministic,
        "non_timing_metrics_deterministic": non_timing_metrics_deterministic,
        "elapsed_ms": elapsed_ms,
    }
    return metrics


def baseline_failures(report: Mapping[str, Any]) -> list[str]:
    """Return human-readable violations of the v2.1 release baseline."""

    metrics = report.get("metrics")
    if not isinstance(metrics, Mapping):
        return ["metrics are missing"]
    failures: list[str] = []

    def require_rate(name: str, expected: float) -> None:
        entry = metrics.get(name)
        rate = entry.get("rate") if isinstance(entry, Mapping) else None
        total = entry.get("total") if isinstance(entry, Mapping) else None
        if not isinstance(total, int) or total <= 0:
            failures.append(f"{name} has no benchmark cases")
        elif rate != expected:
            failures.append(f"{name} expected {expected:.0%}, got {rate!r}")

    require_rate("normal_exact_match", 1.0)
    require_rate("recoverable_recovery", 1.0)
    require_rate("invalid_candidate_acceptance", 0.0)
    require_rate("irrecoverable_correct_failure", 1.0)
    require_rate("expected_stage_match", 1.0)
    if metrics.get("external_network_calls") != 0:
        failures.append("external network calls must remain zero")
    if metrics.get("real_model_calls") != 0:
        failures.append("real model calls must remain zero")
    if metrics.get("artifact_hashes_deterministic") is not True:
        failures.append("artifact hashes changed between identical replays")
    if metrics.get("non_timing_metrics_deterministic") is not True:
        failures.append("non-timing metrics changed between identical replays")
    return failures


def check_baseline(report: Mapping[str, Any]) -> bool:
    """Return ``True`` only when every v2.1 release gate is met."""

    return not baseline_failures(report)


def deterministic_projection(report: Mapping[str, Any]) -> dict[str, Any]:
    """Strip informational timings so two benchmark reports can be compared."""

    cases = []
    raw_cases = report.get("cases", [])
    if isinstance(raw_cases, list):
        for case in raw_cases:
            if isinstance(case, Mapping):
                cases.append({key: value for key, value in case.items() if key != "duration_ms"})
    metrics = report.get("metrics", {})
    stable_metrics = (
        {key: value for key, value in metrics.items() if key != "elapsed_ms"}
        if isinstance(metrics, Mapping)
        else {}
    )
    return {
        "schema_version": report.get("schema_version"),
        "corpus_id": report.get("corpus_id"),
        "corpus_hash": report.get("corpus_hash"),
        "cases": cases,
        "metrics": stable_metrics,
    }


async def run_benchmark_async(
    corpus_path: str | Path | None = None,
    *,
    fixture_root: str | Path | None = None,
) -> dict[str, Any]:
    """Replay the synthetic corpus twice and return a release-gate report."""

    corpus, root, manifest_hash = _load_corpus(corpus_path, fixture_root)
    corpus_id = str(corpus["corpus_id"])
    raw_cases = corpus["cases"]
    counters = {
        "external_network_calls": 0,
        "real_model_calls": 0,
        "fixture_candidate_calls": 0,
    }
    started = time.perf_counter_ns()
    with TemporaryDirectory(prefix="js-benchmark-first-") as first_directory, TemporaryDirectory(
        prefix="js-benchmark-second-"
    ) as second_directory:
        with ExperienceStore(Path(first_directory) / "episodes.sqlite3") as first_store, ExperienceStore(
            Path(second_directory) / "episodes.sqlite3"
        ) as second_store:
            with _offline_guard(counters):
                first = [
                    await _execute_case(case, root, corpus_id, counters, first_store)
                    for case in raw_cases
                ]
                first_candidate_calls = counters["fixture_candidate_calls"]
                second = [
                    await _execute_case(case, root, corpus_id, counters, second_store)
                    for case in raw_cases
                ]
    # The second replay is for determinism verification, not an additional
    # provider-call count in the published first-run metrics.
    counters["fixture_candidate_calls"] = first_candidate_calls
    elapsed_ms = round((time.perf_counter_ns() - started) / 1_000_000, 3)
    artifact_determinism = [case["artifact_hash"] for case in first] == [
        case["artifact_hash"] for case in second
    ]
    first_stable = [
        {key: value for key, value in case.items() if key != "duration_ms"}
        for case in first
    ]
    second_stable = [
        {key: value for key, value in case.items() if key != "duration_ms"}
        for case in second
    ]
    non_timing_determinism = first_stable == second_stable
    fixture_hashes = [case["fixture_hash"] for case in first]
    corpus_hash = _sha256(
        _canonical_json({"manifest": manifest_hash, "fixtures": fixture_hashes})
    )
    metrics = _build_metrics(
        first,
        counters,
        artifact_hashes_deterministic=artifact_determinism,
        non_timing_metrics_deterministic=non_timing_determinism,
        elapsed_ms=elapsed_ms,
    )
    report: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "corpus_id": corpus_id,
        "corpus_hash": corpus_hash,
        "cases": first,
        "metrics": metrics,
    }
    failures = baseline_failures(report)
    report["passed"] = not failures
    report["baseline_failures"] = failures
    return report


def run_benchmark(
    corpus_path: str | Path | None = None,
    *,
    fixture_root: str | Path | None = None,
) -> dict[str, Any]:
    """Synchronous benchmark entry point used by command-line integrations."""

    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(run_benchmark_async(corpus_path, fixture_root=fixture_root))
    raise RuntimeError("run_benchmark() cannot run inside an active event loop; await run_benchmark_async().")


def benchmark_runner(args: Any) -> dict[str, Any]:
    """Argparse-shaped adapter for :mod:`core.crawler_cli`."""

    suite = getattr(args, "suite", None)
    fixture_root = getattr(args, "fixture_root", None)
    return run_benchmark(suite, fixture_root=fixture_root)


__all__ = [
    "BenchmarkConfigurationError",
    "DEFAULT_CORPUS_ID",
    "OfflineBoundaryError",
    "SCHEMA_VERSION",
    "baseline_failures",
    "benchmark_runner",
    "check_baseline",
    "deterministic_projection",
    "run_benchmark",
    "run_benchmark_async",
]

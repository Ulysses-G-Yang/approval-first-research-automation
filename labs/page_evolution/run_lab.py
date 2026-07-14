from __future__ import annotations

"""Run the Page Evolution Lab entirely from checked-in local fixtures."""

import argparse
import asyncio
import json
import re
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Dict
from unittest.mock import patch

from core.spider_engine import GenericSpider


LAB_ROOT = Path(__file__).resolve().parent
FIXTURES = LAB_ROOT / "fixtures"
ORIGINAL_SELECTOR = ".product-card .product-title"


@dataclass(frozen=True)
class LabResult:
    fixture: str
    path: str
    value: str
    recovery: str
    network_access: bool = False


class FixtureElement:
    def __init__(self, text: str):
        self.text = text

    async def inner_text(self) -> str:
        return self.text

    async def get_attribute(self, _name: str) -> str:
        return ""


class FixturePage:
    """A tiny Playwright-shaped page adapter backed by local fixture text."""

    url = "https://page-evolution.invalid/local-fixture"

    def __init__(self, html: str, elements: Dict[str, str]):
        self.html = html
        self.elements = {selector: FixtureElement(value) for selector, value in elements.items()}

    async def query_selector(self, selector: str):
        return self.elements.get(selector)

    async def content(self) -> str:
        return self.html


class FixtureAdaptiveSelector:
    """Deterministic stand-in that makes the adaptive control path observable offline.

    It is intentionally not a Scrapling benchmark. Production adaptive behavior
    is covered by the engine integration and should be validated against approved
    targets separately.
    """

    def __init__(self, html: str, **_kwargs: Any):
        self.html = html

    def css(self, selector: str, *, adaptive: bool = False, **_kwargs: Any):
        if adaptive and selector == ORIGINAL_SELECTOR and 'data-field="title"' in self.html:
            return ["Northwind notebook"]
        return []


class FixtureLLMRepair:
    """A reviewed, deterministic selector candidate for the local teaching fixture."""

    async def repair_selector(self, **_kwargs: Any) -> str:
        return ".catalog-item .headline"


def _fixture(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def _inline_state(html: str) -> Dict[str, Any]:
    match = re.search(r'<script id="page-state" type="application/json">\s*(.*?)\s*</script>', html, flags=re.S)
    if not match:
        raise ValueError("The local v3 fixture did not contain an inline JSON state block.")
    value = json.loads(match.group(1))
    if not isinstance(value, dict):
        raise ValueError("The local v3 fixture state must be a JSON object.")
    return value


async def run_lab() -> list[LabResult]:
    """Exercise configured, adaptive, and candidate-selector paths without network access."""
    v1 = _fixture("catalog-v1.html")
    v1_page = FixturePage(v1, {ORIGINAL_SELECTOR: "Northwind notebook"})
    configured = GenericSpider({"enable_adaptive": True})
    with patch("core.spider_engine.ScraplingSelector", FixtureAdaptiveSelector):
        v1_value = await configured._extract_field_adaptive(
            v1_page,
            {"name": "title", "selector": ORIGINAL_SELECTOR, "description": "catalog title"},
        )

    v2 = _fixture("catalog-v2.html")
    v2_page = FixturePage(v2, {})
    adaptive = GenericSpider({"enable_adaptive": True})
    with patch("core.spider_engine.ScraplingSelector", FixtureAdaptiveSelector):
        v2_value = await adaptive._extract_field_adaptive(
            v2_page,
            {"name": "title", "selector": ORIGINAL_SELECTOR, "description": "catalog title"},
        )

    v3 = _fixture("catalog-v3.html")
    state = _inline_state(v3)
    v3_page = FixturePage(v3, {".catalog-item .headline": "Northwind notebook"})
    candidate = GenericSpider({"enable_adaptive": False})
    candidate.enable_llm_repair = True
    candidate.llm_repair = FixtureLLMRepair()
    v3_value = await candidate._extract_field_adaptive(
        v3_page,
        {"name": "title", "selector": ORIGINAL_SELECTOR, "description": "catalog title"},
    )

    if state.get("schema_version") != 3 or not state.get("records"):
        raise ValueError("The local v3 fixture state does not match the expected teaching schema.")

    return [
        LabResult("catalog-v1.html", "configured_selector", v1_value, "configured selector matched"),
        LabResult("catalog-v2.html", "adaptive_fixture", v2_value, "offline adaptive fallback matched"),
        LabResult("catalog-v3.html", "candidate_selector", v3_value, "mocked candidate matched after state-schema review"),
    ]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run local Page Evolution Lab fixtures without network access.")
    parser.add_argument("--json", action="store_true", help="Print machine-readable results.")
    args = parser.parse_args(argv)
    results = [asdict(result) for result in asyncio.run(run_lab())]
    if args.json:
        print(json.dumps(results, ensure_ascii=False, indent=2))
    else:
        print("Page Evolution Lab (local fixtures only)")
        for result in results:
            print(f"[OK] {result['fixture']}: {result['path']} -> {result['value']}")
        print("No browser page, third-party URL, provider request, login, or publication was used.")
    return 0 if all(result["value"] for result in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())

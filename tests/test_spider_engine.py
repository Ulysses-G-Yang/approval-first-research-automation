from __future__ import annotations

import unittest
from pathlib import Path
from unittest.mock import patch

from scrapling.parser import Selector

from core.spider_engine import GenericSpider


FIXTURES = Path(__file__).parent / "fixtures"


class FakeElement:
    def __init__(self, text: str = "", attributes: dict[str, str] | None = None):
        self.text = text
        self.attributes = attributes or {}

    async def inner_text(self) -> str:
        return self.text

    async def get_attribute(self, name: str) -> str:
        return self.attributes.get(name, "")


class FakePage:
    def __init__(self, html: str, elements: dict[str, FakeElement] | None = None):
        self.html = html
        self.elements = elements or {}
        self.url = "https://example.test/list"

    async def query_selector(self, selector: str):
        return self.elements.get(selector)

    async def content(self) -> str:
        return self.html


class FakeAdaptiveSelector:
    """A deterministic offline stand-in for Scrapling's adaptive branch."""

    def __init__(self, html: str, **_: object):
        self.html = html

    def css(self, selector: str, *, adaptive: bool = False, **__: object):
        if adaptive and selector.startswith(".broken-") and 'data-field="title"' in self.html:
            return ["Example title"]
        return []


class GenericSpiderExtractionTests(unittest.IsolatedAsyncioTestCase):
    def test_fixture_supports_regular_css_selector(self) -> None:
        html = (FIXTURES / "listing-v1.html").read_text(encoding="utf-8")
        values = Selector(html).css(".title::text").getall()
        self.assertEqual(values, ["Example title"])

    async def test_regular_selector_wins_before_fallback(self) -> None:
        html = (FIXTURES / "listing-v1.html").read_text(encoding="utf-8")
        page = FakePage(html, {".title": FakeElement("Example title")})
        spider = GenericSpider({"enable_adaptive": True})

        with patch("core.spider_engine.ScraplingSelector", FakeAdaptiveSelector):
            value = await spider._extract_field_adaptive(page, {"name": "title", "selector": ".title"})

        self.assertEqual(value, "Example title")

    async def test_adaptive_selector_recovers_broken_selector(self) -> None:
        html = (FIXTURES / "listing-v2.html").read_text(encoding="utf-8")
        page = FakePage(html)
        spider = GenericSpider({"enable_adaptive": True})

        with patch("core.spider_engine.ScraplingSelector", FakeAdaptiveSelector):
            value = await spider._extract_field_adaptive(
                page,
                {"name": "title", "selector": ".broken-title-selector"},
            )

        self.assertEqual(value, "Example title")

    async def test_disabled_adaptive_returns_empty_value(self) -> None:
        html = (FIXTURES / "listing-v2.html").read_text(encoding="utf-8")
        page = FakePage(html)
        spider = GenericSpider({"enable_adaptive": False})

        with patch("core.spider_engine.ScraplingSelector", FakeAdaptiveSelector):
            value = await spider._extract_field_adaptive(
                page,
                {"name": "title", "selector": ".broken-title-selector"},
            )

        self.assertEqual(value, "")

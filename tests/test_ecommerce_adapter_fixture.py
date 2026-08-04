from __future__ import annotations

import unittest
from pathlib import Path

from scrapling.parser import Selector

from adapters.ecommerce import ECommerceAdapter
from core.spider_engine import GenericSpider


FIXTURE = Path(__file__).parent / "fixtures" / "ecommerce-adapter.html"


class FixtureElement:
    def __init__(self, node):
        self.node = node

    async def query_selector(self, selector: str):
        try:
            result = self.node.css(selector)
        except Exception:
            return None
        first = getattr(result, "first", None)
        return FixtureElement(first) if first is not None else None

    async def inner_text(self) -> str:
        text = getattr(self.node, "text", "")
        return str(text).strip()

    async def get_attribute(self, name: str) -> str:
        attributes = getattr(self.node, "attrib", {})
        getter = getattr(attributes, "get", None)
        return str(getter(name, "")) if callable(getter) else ""


class FixturePage(FixtureElement):
    url = "https://fixture.invalid/catalog"

    def __init__(self, html: str):
        super().__init__(Selector(html))

    async def query_selector_all(self, selector: str):
        return [FixtureElement(node) for node in self.node.css(selector)]

    async def content(self) -> str:
        return FIXTURE.read_text(encoding="utf-8")


class ECommerceAdapterFixtureTests(unittest.IsolatedAsyncioTestCase):
    async def test_limited_adapter_extracts_only_the_owned_synthetic_fixture(self) -> None:
        adapter = ECommerceAdapter()
        self.assertEqual(adapter.capability_status, "Limited")
        spider = GenericSpider.from_adapter(
            adapter,
            FixturePage.url,
            item_selector="[data-component='product']",
            enable_adaptive=False,
            pagination={"enabled": False, "max_pages": 1},
        )
        records = await spider._extract_fields(
            FixturePage(FIXTURE.read_text(encoding="utf-8")),
            {"page_url": FixturePage.url},
        )
        processed = adapter.post_process(records)

        self.assertEqual(len(processed), 1)
        self.assertEqual(processed[0]["商品标题"], "Northwind notebook")
        self.assertEqual(processed[0]["价格"], 19.9)
        self.assertEqual(processed[0]["商品图片"], "https://fixture.invalid/images/notebook.png")
        self.assertEqual(processed[0]["商品链接"], "https://fixture.invalid/product/notebook")
        self.assertEqual(processed[0]["店铺名称"], "Northwind fixture shop")
        self.assertEqual(processed[0]["评分"], 4.8)


if __name__ == "__main__":
    unittest.main()

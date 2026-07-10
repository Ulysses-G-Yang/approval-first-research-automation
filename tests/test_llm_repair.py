from __future__ import annotations

import asyncio
import unittest
from unittest.mock import patch

from core.llm_repair import LLMRepair


class FakePage:
    url = "https://example.test/list"

    async def content(self) -> str:
        return "<main><h1>Example title</h1></main>"


class LLMRepairTests(unittest.IsolatedAsyncioTestCase):
    def make_repair(self, provider: str = "gemini", timeout: float = 0.1) -> LLMRepair:
        with patch.object(LLMRepair, "_load_api_key", return_value="test-key"):
            repair = LLMRepair(
                {
                    "enable_repair": True,
                    "provider": provider,
                    "secret_ref": "test-only",
                    "timeout": timeout,
                }
            )
        if provider == "gemini":
            repair._gemini_client = object()
        else:
            repair._dashscope = object()
        return repair

    async def test_selector_is_cached_per_page_and_field(self) -> None:
        repair = self.make_repair()
        page = FakePage()
        with patch.object(repair, "_repair_with_gemini", return_value=".title") as call:
            first = await repair.repair_selector(page, "title", "标题", ".old", "<h1>Example title</h1>")
            second = await repair.repair_selector(page, "title", "标题", ".old", "<h1>Example title</h1>")

        self.assertEqual(first, ".title")
        self.assertEqual(second, ".title")
        self.assertEqual(call.call_count, 1)

    async def test_empty_provider_response_degrades_to_empty_selector(self) -> None:
        repair = self.make_repair("qwen")
        with patch.object(repair, "_repair_with_qwen", return_value=""):
            value = await repair.repair_selector(FakePage(), "title", "标题", ".old", "<h1>Example</h1>")
        self.assertEqual(value, "")

    async def test_invalid_provider_response_is_rejected(self) -> None:
        repair = self.make_repair()
        with patch.object(repair, "_repair_with_gemini", return_value="!not-a-css-selector"):
            value = await repair.repair_selector(FakePage(), "title", "标题", ".old", "<h1>Example</h1>")
        self.assertEqual(value, "")

    async def test_timeout_degrades_to_empty_selector(self) -> None:
        repair = self.make_repair(timeout=0.01)

        async def never_to_thread(*_: object, **__: object) -> str:
            await asyncio.Future()
            return ""

        with patch("core.llm_repair.asyncio.to_thread", new=never_to_thread):
            value = await repair.repair_selector(FakePage(), "title", "标题", ".old", "<h1>Example</h1>")
        self.assertEqual(value, "")

    def test_fenced_css_response_is_parsed(self) -> None:
        self.assertEqual(LLMRepair._extract_selector("```css\n.card .title\n```"), ".card .title")

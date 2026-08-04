from __future__ import annotations

import asyncio
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

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
    def test_security_relevant_switches_require_literal_booleans(self) -> None:
        invalid_configs = [
            {"llm": {"enable_repair": "false"}},
            {"repair_memory": {"enabled": "false"}},
            {"retain_full_episode_content": "false"},
        ]
        for extra in invalid_configs:
            with self.subTest(extra=extra), self.assertRaises(TypeError):
                GenericSpider({"start_url": "https://example.test", **extra})

    async def test_unconfigured_goto_preserves_playwright_default_wait_state(self) -> None:
        class GotoPage:
            def __init__(self):
                self.kwargs = None

            async def goto(self, _url: str, **kwargs):
                self.kwargs = kwargs

        page = GotoPage()
        spider = GenericSpider({"start_url": "https://example.test"})
        await spider._goto_page(page, "https://example.test")
        self.assertEqual(page.kwargs, {"timeout": 30000})

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

    async def test_scrapling_adaptive_state_is_private_and_memory_only(self) -> None:
        constructed: list[dict[str, object]] = []

        class RecordingSelector:
            def __init__(self, _html: str, **kwargs: object):
                constructed.append(kwargs)

            def css(self, _selector: str, *, adaptive: bool = False, **_kwargs):
                return ["memory-only"] if adaptive else []

        page = FakePage("<main>fixture</main>")
        spider = GenericSpider({"enable_adaptive": True})
        with patch("core.spider_engine.ScraplingSelector", RecordingSelector):
            value = await spider._extract_field_adaptive(
                page,
                {"name": "title", "selector": ".missing"},
            )

        self.assertEqual(value, "memory-only")
        storage = constructed[0]["_storage"]
        self.assertEqual(getattr(storage, "storage_file"), ":memory:")
        second = GenericSpider({"enable_adaptive": True})
        self.assertIsNot(
            storage,
            second._scrapling_storage_for_url(page.url),
        )
        spider._close_scrapling_storages()
        second._close_scrapling_storages()

    def test_start_urls_are_stably_deduplicated(self) -> None:
        spider = GenericSpider(
            {
                "start_url": "https://one.invalid/",
                "start_urls": [
                    "https://one.invalid/",
                    "https://two.invalid/",
                    "https://two.invalid/",
                ],
            }
        )
        self.assertEqual(
            spider.start_urls,
            ["https://one.invalid/", "https://two.invalid/"],
        )

    async def test_legacy_source_keeps_pre_v21_precedence_over_selector(self) -> None:
        page = FakePage(
            "<main>fixture</main>",
            {".fallback": FakeElement("selector-value")},
        )
        spider = GenericSpider(
            {
                "enable_adaptive": False,
                "fields": [
                    {
                        "name": "title",
                        "source": "legacy_payload.title",
                        "selector": ".fallback",
                    }
                ],
            }
        )

        records = await spider._extract_fields(page, {"legacy_payload": {}})

        self.assertEqual(records, [{"title": ""}])

    async def test_scrapling_cache_is_cleared_between_paginated_pages(self) -> None:
        class VersionedSelector:
            def __init__(self, html: str, **_kwargs):
                self.html = html

            def css(self, _selector: str, *, adaptive: bool = False, **_kwargs):
                if not adaptive:
                    return []
                return ["Second page"] if "second-version" in self.html else ["First page"]

        class NextButton:
            def __init__(self, page):
                self.page = page

            async def get_attribute(self, _name: str):
                return None

            async def click(self) -> None:
                self.page.page_number = 2
                self.page.html = "<main>second-version</main>"
                self.page.url = "https://example.test/list?page=2"

        class PaginatedPage(FakePage):
            def __init__(self):
                super().__init__("<main>first-version</main>")
                self.page_number = 1

            async def goto(self, url: str) -> None:
                self.url = url

            async def wait_for_load_state(self, **_kwargs) -> None:
                return None

            async def wait_for_timeout(self, _delay: int) -> None:
                return None

            async def query_selector(self, selector: str):
                if selector == ".next":
                    return NextButton(self) if self.page_number == 1 else None
                return await super().query_selector(selector)

        page = PaginatedPage()
        spider = GenericSpider(
            {
                "start_url": page.url,
                "enable_adaptive": True,
                "pagination": {
                    "enabled": True,
                    "max_pages": 2,
                    "next_selector": ".next",
                },
                "fields": [{"name": "title", "selector": ".broken"}],
            }
        )

        with patch("core.spider_engine.ScraplingSelector", VersionedSelector):
            records = await spider._crawl_pages(page, page.url)

        self.assertEqual(
            [record["title"] for record in records],
            ["First page", "Second page"],
        )

    async def test_optional_network_policy_is_installed_without_changing_standalone_config(self) -> None:
        class FakePageForRun:
            async def close(self) -> None:
                return None

        class FakeContextForRun:
            async def new_page(self):
                return FakePageForRun()

            async def close(self) -> None:
                return None

        class FakeBrowser:
            contexts = []

            def __init__(self):
                self.context = FakeContextForRun()
                self.context_kwargs = None

            async def new_context(self, **kwargs):
                self.context_kwargs = kwargs
                return self.context

            async def close(self) -> None:
                return None

        browser = FakeBrowser()
        launch = AsyncMock(return_value=browser)

        class FakePlaywrightManager:
            async def __aenter__(self):
                chromium = SimpleNamespace(launch=launch)
                return SimpleNamespace(chromium=chromium)

            async def __aexit__(self, *_args):
                return None

        policy = SimpleNamespace(
            context_options={"accept_downloads": False, "service_workers": "block"},
            launch_options={"args": ["--disable-quic", "--force-webrtc-ip-handling-policy=disable_non_proxied_udp"]},
            install=AsyncMock(),
        )
        spider = GenericSpider(
            {"start_url": "https://example.com", "browser": {"headless": True}},
            network_policy=policy,
        )

        with patch("core.spider_engine.async_playwright", return_value=FakePlaywrightManager()), patch.object(
            spider, "_crawl_pages", new=AsyncMock(return_value=[])
        ):
            await spider.run()

        self.assertEqual(
            browser.context_kwargs,
            {"accept_downloads": False, "service_workers": "block"},
        )
        policy.install.assert_awaited_once_with(browser.context)
        launch.assert_awaited_once()
        launch_args = launch.await_args.kwargs["args"]
        self.assertIn("--disable-quic", launch_args)
        self.assertIn("--force-webrtc-ip-handling-policy=disable_non_proxied_udp", launch_args)

    async def test_browser_is_closed_when_context_creation_is_cancelled(self) -> None:
        class SlowBrowser:
            contexts = []

            def __init__(self):
                self.closed = False

            async def new_context(self, **_kwargs):
                await asyncio.sleep(1)

            async def close(self) -> None:
                self.closed = True

        browser = SlowBrowser()

        class FakePlaywrightManager:
            async def __aenter__(self):
                return SimpleNamespace(chromium=SimpleNamespace(launch=AsyncMock(return_value=browser)))

            async def __aexit__(self, *_args):
                return None

        spider = GenericSpider({"start_url": "https://example.com", "browser": {"headless": True}})
        with patch("core.spider_engine.async_playwright", return_value=FakePlaywrightManager()):
            with self.assertRaises(asyncio.TimeoutError):
                await asyncio.wait_for(spider.run(), timeout=0.01)
        self.assertTrue(browser.closed)

    async def test_context_and_browser_close_when_policy_install_fails(self) -> None:
        class FakeContext:
            def __init__(self):
                self.closed = False

            async def close(self) -> None:
                self.closed = True

        class FakeBrowser:
            contexts = []

            def __init__(self):
                self.context = FakeContext()
                self.closed = False

            async def new_context(self, **_kwargs):
                return self.context

            async def close(self) -> None:
                self.closed = True

        browser = FakeBrowser()

        class FakePlaywrightManager:
            async def __aenter__(self):
                return SimpleNamespace(chromium=SimpleNamespace(launch=AsyncMock(return_value=browser)))

            async def __aexit__(self, *_args):
                return None

        policy = SimpleNamespace(
            context_options={"accept_downloads": False, "service_workers": "block"},
            launch_options={"args": ["--disable-quic"]},
            install=AsyncMock(side_effect=RuntimeError("policy install failed")),
        )
        spider = GenericSpider(
            {"start_url": "https://example.com", "browser": {"headless": True}},
            network_policy=policy,
        )
        with patch("core.spider_engine.async_playwright", return_value=FakePlaywrightManager()):
            with self.assertRaisesRegex(RuntimeError, "policy install failed"):
                await spider.run()
        self.assertTrue(browser.context.closed)
        self.assertTrue(browser.closed)

    async def test_timeout_is_not_held_open_by_stuck_browser_cleanup(self) -> None:
        class SlowPage:
            def __init__(self):
                self.close_started = False

            async def close(self) -> None:
                self.close_started = True
                await asyncio.sleep(1)

        class SlowContext:
            def __init__(self):
                self.page = SlowPage()
                self.close_started = False

            async def new_page(self):
                return self.page

            async def close(self) -> None:
                self.close_started = True
                await asyncio.sleep(1)

        class SlowBrowser:
            contexts = []

            def __init__(self):
                self.context = SlowContext()
                self.close_started = False

            async def new_context(self, **_kwargs):
                return self.context

            async def close(self) -> None:
                self.close_started = True
                await asyncio.sleep(1)

        browser = SlowBrowser()

        class FakePlaywrightManager:
            async def __aenter__(self):
                return SimpleNamespace(chromium=SimpleNamespace(launch=AsyncMock(return_value=browser)))

            async def __aexit__(self, *_args):
                return None

        spider = GenericSpider({"start_url": "https://example.com", "browser": {"headless": True}})
        loop = asyncio.get_running_loop()
        started = loop.time()
        with patch("core.spider_engine.async_playwright", return_value=FakePlaywrightManager()), patch(
            "core.spider_engine._BROWSER_CLEANUP_TIMEOUT_SECONDS", 0.01
        ), patch.object(spider, "_crawl_pages", new=AsyncMock(return_value=[])):
            with self.assertRaises(asyncio.TimeoutError):
                await asyncio.wait_for(spider.run(), timeout=0.01)

        self.assertLess(loop.time() - started, 0.15)
        self.assertTrue(browser.context.page.close_started)
        self.assertTrue(browser.context.close_started)
        self.assertTrue(browser.close_started)
        self.assertEqual(
            [
                task
                for task in asyncio.all_tasks()
                if task is not asyncio.current_task()
                and not task.done()
                and task.get_name().startswith("crawler-close:")
            ],
            [],
        )

    async def test_playwright_manager_is_stopped_when_startup_is_cancelled(self) -> None:
        class InterruptedPlaywrightManager:
            def __init__(self):
                self.enter_started = False
                self.exit_called = False

            async def __aenter__(self):
                self.enter_started = True
                await asyncio.Event().wait()

            async def __aexit__(self, *_args):
                self.exit_called = True

        manager = InterruptedPlaywrightManager()
        spider = GenericSpider({"start_url": "https://example.com", "browser": {"headless": True}})
        with patch("core.spider_engine.async_playwright", return_value=manager):
            with self.assertRaises(asyncio.TimeoutError):
                await asyncio.wait_for(spider.run(), timeout=0.01)

        self.assertTrue(manager.enter_started)
        self.assertTrue(manager.exit_called)

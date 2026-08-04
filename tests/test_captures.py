from __future__ import annotations

import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from core.captures import (
    CaptureConfigurationError,
    PageCaptureSession,
    RequiredCaptureError,
    parse_capture_specs,
)
from core.experience_store import ExperienceStore
from core.spider_engine import GenericSpider


class FakeElement:
    def __init__(self, value: str):
        self.value = value

    async def text_content(self) -> str:
        return self.value

    async def inner_text(self) -> str:
        return self.value

    async def get_attribute(self, _name: str) -> str:
        return ""


class FakePage:
    def __init__(self, elements: dict[str, str] | None = None):
        self.elements = elements or {}
        self.listeners: dict[str, list] = {}
        self.url = "https://fixture.invalid/page"

    def on(self, event: str, callback) -> None:
        self.listeners.setdefault(event, []).append(callback)

    def remove_listener(self, event: str, callback) -> None:
        self.listeners[event].remove(callback)

    async def query_selector(self, selector: str):
        value = self.elements.get(selector)
        return FakeElement(value) if value is not None else None

    def emit_response(self, response) -> None:
        for callback in self.listeners.get("response", []):
            callback(response)

    async def goto(self, url: str) -> None:
        self.url = url

    async def wait_for_load_state(self, **_kwargs) -> None:
        return None

    async def content(self) -> str:
        return "<html><body>fixture</body></html>"


class FakeResponse:
    def __init__(
        self,
        url: str,
        payload,
        *,
        method: str = "GET",
        status: int = 200,
        resource_type: str = "fetch",
        content_type: str = "application/json; charset=utf-8",
    ):
        self.url = url
        self.status = status
        encoded = json.dumps(payload).encode("utf-8")
        self.headers = {
            "content-type": content_type,
            "content-length": str(len(encoded)),
        }
        self.request = SimpleNamespace(method=method, resource_type=resource_type)
        self.payload = payload

    async def body(self) -> bytes:
        await asyncio.sleep(0)
        return json.dumps(self.payload).encode("utf-8")


class HangingResponse(FakeResponse):
    async def body(self) -> bytes:
        await asyncio.Event().wait()
        return b"{}"


class CaptureConfigurationTests(unittest.TestCase):
    def test_old_configs_need_no_captures(self) -> None:
        self.assertEqual(parse_capture_specs(None), ())
        self.assertEqual(parse_capture_specs([]), ())

    def test_capture_names_and_required_arguments_are_validated(self) -> None:
        with self.assertRaisesRegex(CaptureConfigurationError, "selector is required"):
            parse_capture_specs(
                [
                    {
                        "name": "state",
                        "type": "embedded_json",
                        "required": False,
                        "max_bytes": 1024,
                    }
                ]
            )
        with self.assertRaisesRegex(CaptureConfigurationError, "unique"):
            parse_capture_specs(
                [
                    {
                        "name": "state",
                        "type": "embedded_json",
                        "selector": "#one",
                        "required": False,
                        "max_bytes": 1024,
                    },
                    {
                        "name": "state",
                        "type": "network_json",
                        "url_glob": "*/api",
                        "required": False,
                        "max_bytes": 1024,
                    },
                ]
            )
        for invalid_max_bytes in (True, 1.9, "1024"):
            with self.subTest(max_bytes=invalid_max_bytes), self.assertRaisesRegex(
                CaptureConfigurationError,
                "must be an integer",
            ):
                parse_capture_specs(
                    [
                        {
                            "name": "state",
                            "type": "embedded_json",
                            "selector": "#state",
                            "required": False,
                            "max_bytes": invalid_max_bytes,
                        }
                    ]
                )

        for reserved_name in ("page_url", "bootstrap.state"):
            with self.subTest(name=reserved_name), self.assertRaises(
                CaptureConfigurationError
            ):
                parse_capture_specs(
                    [
                        {
                            "name": reserved_name,
                            "type": "embedded_json",
                            "selector": "#state",
                            "required": False,
                            "max_bytes": 1024,
                        }
                    ]
                )

    def test_capture_name_cannot_override_an_action_result_key(self) -> None:
        with self.assertRaisesRegex(CaptureConfigurationError, "result_key"):
            GenericSpider(
                {
                    "actions": [
                        {"type": "evaluate", "script": "() => ({})", "result_key": "state"}
                    ],
                    "captures": [
                        {
                            "name": "state",
                            "type": "embedded_json",
                            "selector": "#state",
                            "required": False,
                            "max_bytes": 1024,
                        }
                    ],
                }
            )


class PageCaptureSessionTests(unittest.IsolatedAsyncioTestCase):
    async def test_embedded_and_first_matching_network_json_are_captured(self) -> None:
        specs = parse_capture_specs(
            [
                {
                    "name": "bootstrap",
                    "type": "embedded_json",
                    "selector": "#state",
                    "required": True,
                    "max_bytes": 1024,
                },
                {
                    "name": "catalog",
                    "type": "network_json",
                    "url_glob": "https://fixture.invalid/api/*",
                    "required": True,
                    "max_bytes": 1024,
                },
            ]
        )
        page = FakePage({"#state": '{"props":{"title":"Embedded"}}'})
        session = PageCaptureSession(page, specs, timeout_ms=25)
        session.install()
        page.emit_response(FakeResponse("https://fixture.invalid/other", {"ignored": True}))
        page.emit_response(FakeResponse("https://fixture.invalid/api/items", {"items": ["first"]}))
        page.emit_response(FakeResponse("https://fixture.invalid/api/items", {"items": ["second"]}))

        values = await session.finish()
        session.close()

        self.assertEqual(values["bootstrap"]["props"]["title"], "Embedded")
        self.assertEqual(values["catalog"], {"items": ["first"]})
        self.assertEqual(page.listeners["response"], [])

    async def test_non_get_non_2xx_non_xhr_and_non_json_responses_are_ignored(self) -> None:
        specs = parse_capture_specs(
            [
                {
                    "name": "catalog",
                    "type": "network_json",
                    "url_glob": "*/api/*",
                    "required": True,
                    "max_bytes": 1024,
                }
            ]
        )
        page = FakePage()
        session = PageCaptureSession(page, specs, timeout_ms=25)
        session.install()
        page.emit_response(FakeResponse("https://x/api/one", {}, method="POST"))
        page.emit_response(FakeResponse("https://x/api/two", {}, status=404))
        page.emit_response(FakeResponse("https://x/api/three", {}, resource_type="document"))
        page.emit_response(FakeResponse("https://x/api/four", {}, content_type="text/plain"))
        with self.assertRaisesRegex(RequiredCaptureError, "timed out after 25ms"):
            await session.finish()

    async def test_required_network_capture_waits_for_a_delayed_first_response(self) -> None:
        page = FakePage()
        specs = parse_capture_specs(
            [
                {
                    "name": "catalog",
                    "type": "network_json",
                    "url_glob": "https://fixture.invalid/api/*",
                    "required": True,
                    "max_bytes": 1024,
                }
            ]
        )
        session = PageCaptureSession(page, specs, timeout_ms=250)
        session.install()

        async def emit_later() -> None:
            await asyncio.sleep(0.02)
            page.emit_response(
                FakeResponse(
                    "https://fixture.invalid/api/catalog",
                    {"items": ["delayed"]},
                )
            )

        emitter = asyncio.create_task(emit_later())
        values = await session.finish()
        await emitter
        session.close()
        self.assertEqual(values["catalog"], {"items": ["delayed"]})

    async def test_capture_timeout_requires_a_literal_positive_integer(self) -> None:
        for invalid in (True, 1.5, "25", 0):
            with self.subTest(timeout=invalid), self.assertRaises(CaptureConfigurationError):
                PageCaptureSession(FakePage(), (), timeout_ms=invalid)  # type: ignore[arg-type]

    async def test_size_limit_and_invalid_json_fail_required_capture(self) -> None:
        page = FakePage({"#state": '"' + ("x" * 20) + '"'})
        specs = parse_capture_specs(
            [
                {
                    "name": "state",
                    "type": "embedded_json",
                    "selector": "#state",
                    "required": True,
                    "max_bytes": 8,
                }
            ]
        )
        with self.assertRaisesRegex(RequiredCaptureError, "exceeds max_bytes"):
            await PageCaptureSession(page, specs).finish()

    async def test_hanging_response_body_is_bounded_by_capture_timeout(self) -> None:
        page = FakePage()
        specs = parse_capture_specs(
            [
                {
                    "name": "catalog",
                    "type": "network_json",
                    "url_glob": "https://fixture.invalid/api/*",
                    "required": True,
                    "max_bytes": 1024,
                }
            ]
        )
        session = PageCaptureSession(page, specs, timeout_ms=25)
        session.install()
        page.emit_response(
            HangingResponse("https://fixture.invalid/api/catalog", {"items": []})
        )

        with self.assertRaisesRegex(RequiredCaptureError, "timed out after 25ms"):
            await session.finish()
        session.close()

    async def test_aclose_detaches_listener_and_drains_body_tasks(self) -> None:
        page = FakePage()
        specs = parse_capture_specs(
            [
                {
                    "name": "catalog",
                    "type": "network_json",
                    "url_glob": "https://fixture.invalid/api/*",
                    "required": False,
                    "max_bytes": 1024,
                }
            ]
        )
        session = PageCaptureSession(page, specs)
        session.install()
        page.emit_response(
            HangingResponse("https://fixture.invalid/api/catalog", {"items": []})
        )
        await asyncio.sleep(0)
        body_tasks = tuple(session._tasks)

        await session.aclose()

        self.assertEqual(page.listeners["response"], [])
        self.assertTrue(body_tasks)
        self.assertTrue(all(task.done() for task in body_tasks))

    async def test_chunked_json_without_content_length_is_checked_after_body_read(self) -> None:
        page = FakePage()
        specs = parse_capture_specs(
            [
                {
                    "name": "catalog",
                    "type": "network_json",
                    "url_glob": "https://fixture.invalid/api/*",
                    "required": True,
                    "max_bytes": 1024,
                }
            ]
        )
        response = FakeResponse(
            "https://fixture.invalid/api/catalog",
            {"items": []},
        )
        response.headers.pop("content-length")
        # Leave enough scheduling headroom for a loaded CI event loop; the
        # dedicated hanging-response test above covers the 25ms timeout path.
        session = PageCaptureSession(page, specs, timeout_ms=250)
        session.install()
        page.emit_response(response)

        values = await session.finish()
        self.assertEqual(values["catalog"], {"items": []})
        session.close()

    async def test_generic_spider_exposes_capture_names_to_source_paths(self) -> None:
        class CapturingPage(FakePage):
            async def goto(self, url: str) -> None:
                await super().goto(url)
                self.emit_response(
                    FakeResponse(
                        "https://fixture.invalid/api/catalog",
                        {"payload": {"items": [{"title": "Network title"}]}},
                    )
                )

        page = CapturingPage({"#state": '{"props":{"score":9}}'})
        spider = GenericSpider(
            {
                "start_url": page.url,
                "enable_adaptive": False,
                "captures": [
                    {
                        "name": "bootstrap",
                        "type": "embedded_json",
                        "selector": "#state",
                        "required": True,
                        "max_bytes": 1024,
                    },
                    {
                        "name": "catalog",
                        "type": "network_json",
                        "url_glob": "https://fixture.invalid/api/*",
                        "required": True,
                        "max_bytes": 1024,
                    },
                ],
                "fields": [
                    {"name": "title", "source": "catalog.payload.items.0.title"},
                    {"name": "score", "source": "bootstrap.props.score"},
                ],
            }
        )

        records = await spider._scrape_current_page(page, page.url)

        self.assertEqual(records[0]["title"], "Network title")
        self.assertEqual(records[0]["score"], 9)

    async def test_pagination_installs_network_capture_before_next_click(self) -> None:
        class NextButton:
            def __init__(self, page):
                self.page = page

            async def get_attribute(self, _name: str):
                return None

            async def click(self) -> None:
                self.page.page_number = 2
                self.page.url = "https://fixture.invalid/page/2"
                self.page.emit_response(
                    FakeResponse(
                        "https://fixture.invalid/api/page/2",
                        {"title": "Second page"},
                    )
                )

        class PaginatedPage(FakePage):
            def __init__(self):
                super().__init__()
                self.page_number = 1

            async def goto(self, url: str) -> None:
                await super().goto(url)
                self.emit_response(
                    FakeResponse(
                        "https://fixture.invalid/api/page/1",
                        {"title": "First page"},
                    )
                )

            async def query_selector(self, selector: str):
                if selector == ".next":
                    return NextButton(self) if self.page_number == 1 else None
                return await super().query_selector(selector)

            async def wait_for_timeout(self, _delay: int) -> None:
                return None

        page = PaginatedPage()
        spider = GenericSpider(
            {
                "start_url": page.url,
                "enable_adaptive": False,
                "request": {"timeout_ms": 1000},
                "pagination": {
                    "enabled": True,
                    "max_pages": 2,
                    "next_selector": ".next",
                },
                "captures": [
                    {
                        "name": "catalog",
                        "type": "network_json",
                        "url_glob": "https://fixture.invalid/api/page/*",
                        "required": True,
                        "max_bytes": 1024,
                    }
                ],
                "fields": [{"name": "title", "source": "catalog.title"}],
            }
        )

        records = await spider._crawl_pages(page, page.url)

        self.assertEqual([record["title"] for record in records], ["First page", "Second page"])

    async def test_optional_embedded_playwright_error_does_not_fail_page(self) -> None:
        class BrokenPage(FakePage):
            async def query_selector(self, _selector: str):
                raise RuntimeError("invalid selector from Playwright")

        specs = parse_capture_specs(
            [
                {
                    "name": "bootstrap",
                    "type": "embedded_json",
                    "selector": "[invalid",
                    "required": False,
                    "max_bytes": 1024,
                }
            ]
        )
        session = PageCaptureSession(BrokenPage(), specs)

        self.assertEqual(await session.finish(), {})
        self.assertIn("invalid selector", session.errors["bootstrap"])

    async def test_opt_in_store_records_failed_extraction_episode_and_cas_artifact(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store_path = Path(directory) / "episodes.sqlite3"
            with ExperienceStore(store_path) as store:
                page = FakePage()
                spider = GenericSpider(
                    {
                        "name": "fixture-target",
                        "start_url": page.url,
                        "enable_adaptive": False,
                        "authorization_category": "synthetic_local",
                    },
                    experience_store=store,
                )
                value = await spider._extract_field_adaptive(
                    page,
                    {
                        "name": "title",
                        "selector": ".missing",
                        "validation": {"non_empty": {}},
                    },
                )

                self.assertEqual(value, "")
                self.assertEqual(len(spider.repair_episode_ids), 1)
                episode = store.get_episode(spider.repair_episode_ids[0], include_artifacts=True)
                self.assertEqual(episode["episode"]["metadata"]["failure_stage"], "exhausted")
                self.assertEqual(episode["episode"]["metadata"]["failed_fields"], ["title"])
                capture_events = [
                    event for event in episode["events"] if event["event_type"] == "capture"
                ]
                self.assertEqual(len(capture_events), 1)
                digest = capture_events[0]["artifact_sha256"]
                self.assertTrue((Path(f"{store_path}.cas") / "objects" / digest[:2] / digest[2:4] / digest).is_file())

    async def test_required_capture_failure_records_episode_then_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store_path = Path(directory) / "episodes.sqlite3"
            with ExperienceStore(store_path) as store:
                page = FakePage()
                spider = GenericSpider(
                    {
                        "name": "required-capture-fixture",
                        "start_url": page.url,
                        "authorization_category": "synthetic_local",
                        "enable_adaptive": False,
                        "captures": [
                            {
                                "name": "bootstrap",
                                "type": "embedded_json",
                                "selector": "#missing-state",
                                "required": True,
                                "max_bytes": 1024,
                            }
                        ],
                        "fields": [
                            {"name": "title", "source": "bootstrap.title"}
                        ],
                    },
                    experience_store=store,
                )

                with self.assertRaises(RequiredCaptureError):
                    await spider._scrape_current_page(page)

                self.assertEqual(len(spider.repair_episode_ids), 1)
                payload = store.get_episode(spider.repair_episode_ids[0])
                metadata = payload["episode"]["metadata"]
                self.assertEqual(metadata["failure_stage"], "capture")
                self.assertEqual(metadata["failed_fields"], [])
                self.assertEqual(metadata["failed_captures"], ["bootstrap"])
                self.assertIn("selector did not match", metadata["capture_errors"]["bootstrap"])
                self.assertEqual(len(payload["artifacts"]), 1)

    async def test_navigation_uses_configured_wait_and_timeout_failure_records_episode(self) -> None:
        class TimeoutPage(FakePage):
            def __init__(self):
                super().__init__()
                self.goto_kwargs: dict[str, object] = {}

            async def goto(self, url: str, **kwargs: object) -> None:
                self.url = url
                self.goto_kwargs = kwargs

            async def wait_for_selector(self, _selector: str, **_kwargs: object):
                raise asyncio.TimeoutError("hydration selector drifted")

        with tempfile.TemporaryDirectory() as directory:
            with ExperienceStore(Path(directory) / "episodes.sqlite3") as store:
                page = TimeoutPage()
                spider = GenericSpider(
                    {
                        "name": "wait-fixture",
                        "authorization_category": "synthetic_local",
                        "start_url": page.url,
                        "request": {
                            "wait_until": "domcontentloaded",
                            "wait_for_selector": "#ready",
                            "timeout_ms": 1234,
                        },
                        "fields": [{"name": "title", "selector": "h1"}],
                    },
                    experience_store=store,
                )

                with self.assertRaisesRegex(asyncio.TimeoutError, "selector drifted"):
                    await spider._scrape_current_page(page, page.url)

                self.assertEqual(
                    page.goto_kwargs,
                    {"wait_until": "domcontentloaded", "timeout": 1234},
                )
                payload = store.get_episode(spider.repair_episode_ids[0])
                metadata = payload["episode"]["metadata"]
                self.assertEqual(metadata["failure_stage"], "wait")
                self.assertEqual(metadata["failed_wait_conditions"]["wait_for_selector"], "#ready")

    async def test_paginated_wait_failure_records_episode_and_bounds_click(self) -> None:
        class NextButton:
            def __init__(self, page):
                self.page = page
                self.kwargs: dict[str, object] = {}

            async def get_attribute(self, _name: str):
                return None

            async def click(self, **kwargs: object) -> None:
                self.kwargs = kwargs
                self.page.after_click = True

        class PaginationTimeoutPage(FakePage):
            def __init__(self):
                super().__init__()
                self.after_click = False
                self.next_button = NextButton(self)

            async def goto(self, url: str, **_kwargs: object) -> None:
                self.url = url

            async def wait_for_selector(self, _selector: str, **_kwargs: object):
                if self.after_click:
                    raise asyncio.TimeoutError("second page hydration drifted")
                return FakeElement("ready")

            async def query_selector(self, selector: str):
                if selector == ".next":
                    return self.next_button
                return await super().query_selector(selector)

        with tempfile.TemporaryDirectory() as directory:
            with ExperienceStore(Path(directory) / "episodes.sqlite3") as store:
                page = PaginationTimeoutPage()
                spider = GenericSpider(
                    {
                        "name": "pagination-wait-fixture",
                        "authorization_category": "synthetic_local",
                        "start_url": page.url,
                        "request": {
                            "wait_for_selector": "#ready",
                            "timeout_ms": 2500,
                        },
                        "pagination": {
                            "enabled": True,
                            "max_pages": 2,
                            "next_selector": ".next",
                        },
                    },
                    experience_store=store,
                )

                with self.assertRaisesRegex(asyncio.TimeoutError, "second page"):
                    await spider._crawl_pages(page, page.url)

                self.assertEqual(
                    page.next_button.kwargs,
                    {"timeout": 2500},
                )
                payload = store.get_episode(spider.repair_episode_ids[0])
                self.assertEqual(payload["episode"]["metadata"]["failure_stage"], "wait")

    async def test_item_selector_miss_records_a_repair_episode(self) -> None:
        class EmptyListPage(FakePage):
            async def query_selector_all(self, _selector: str):
                return []

        with tempfile.TemporaryDirectory() as directory:
            with ExperienceStore(Path(directory) / "episodes.sqlite3") as store:
                page = EmptyListPage()
                spider = GenericSpider(
                    {
                        "name": "item-selector-drift",
                        "authorization_category": "synthetic_local",
                        "start_url": page.url,
                        "item_selector": ".product-card",
                        "fields": [
                            {"name": "title", "selector": ".title"},
                            {"name": "price", "selector": ".price"},
                        ],
                    },
                    experience_store=store,
                )

                self.assertEqual(await spider._extract_fields(page, {"page_url": page.url}), [])
                payload = store.get_episode(spider.repair_episode_ids[0])
                metadata = payload["episode"]["metadata"]
                self.assertEqual(metadata["failure_stage"], "item_selector")
                self.assertEqual(metadata["failed_fields"], ["title", "price"])
                self.assertEqual(metadata["item_selector"], ".product-card")

    async def test_source_value_runs_quality_gate_and_records_declared_capture_artifact(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store_path = Path(directory) / "episodes.sqlite3"
            with ExperienceStore(store_path) as store:
                page = FakePage()
                spider = GenericSpider(
                    {
                        "name": "capture-source-fixture",
                        "authorization_category": "synthetic_local",
                        "enable_adaptive": False,
                        "captures": [
                            {
                                "name": "bootstrap",
                                "type": "embedded_json",
                                "selector": "#state",
                                "required": False,
                                "max_bytes": 1024,
                            }
                        ],
                        "fields": [
                            {
                                "name": "status",
                                "source": "bootstrap.status",
                                "validation": {"enum": {"values": ["AVAILABLE"]}},
                            }
                        ],
                    },
                    experience_store=store,
                )

                records = await spider._extract_fields(
                    page,
                    {"bootstrap": {"status": "SALE!"}},
                )

                self.assertEqual(records, [{"status": ""}])
                self.assertEqual(len(spider.repair_episode_ids), 1)
                payload = store.get_episode(spider.repair_episode_ids[0])
                self.assertEqual(payload["episode"]["metadata"]["failure_stage"], "source")
                self.assertFalse(payload["episode"]["metadata"]["quality_gate"]["passed"])
                capture_events = [
                    event for event in payload["events"] if event["event_type"] == "capture"
                ]
                self.assertEqual(
                    [event["payload"]["capture_type"] for event in capture_events],
                    ["page_features", "capture:bootstrap"],
                )
                self.assertEqual(len(payload["artifacts"]), 2)

    async def test_optional_capture_error_is_recorded_in_source_episode(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with ExperienceStore(Path(directory) / "episodes.sqlite3") as store:
                page = FakePage({"#state": "not-json"})
                spider = GenericSpider(
                    {
                        "name": "optional-capture-fixture",
                        "authorization_category": "synthetic_local",
                        "start_url": page.url,
                        "enable_adaptive": False,
                        "captures": [
                            {
                                "name": "bootstrap",
                                "type": "embedded_json",
                                "selector": "#state",
                                "required": False,
                                "max_bytes": 1024,
                            }
                        ],
                        "fields": [
                            {"name": "title", "source": "bootstrap.title"}
                        ],
                    },
                    experience_store=store,
                )

                records = await spider._scrape_current_page(page, page.url)
                self.assertEqual(records[0]["title"], "")
                self.assertIn("crawl_time", records[0])
                payload = store.get_episode(spider.repair_episode_ids[0])
                metadata = payload["episode"]["metadata"]
                self.assertEqual(metadata["failure_stage"], "source")
                self.assertIn("Expecting value", metadata["capture_errors"]["bootstrap"])


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

import unittest

from core.captures import RequiredCaptureError
from core.spider_engine import GenericSpider
from tests.test_browser_network_integration import (
    _RUN_REAL_CHROMIUM,
    _browser_config,
    _fixture_server,
)


@unittest.skipUnless(
    _RUN_REAL_CHROMIUM,
    "set CRAWLER_BROWSER_INTEGRATION=1 after installing Playwright Chromium",
)
class CrawlerCaptureChromiumIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def test_embedded_and_passive_fetch_json_capture_in_real_chromium(self) -> None:
        with _fixture_server() as server:
            root = f"http://127.0.0.1:{server.port}"
            spider = GenericSpider(
                {
                    "start_url": f"{root}/crawler-captures",
                    "browser": _browser_config(),
                    "request": {
                        "timeout_ms": 5_000,
                        "wait_until": "load",
                        "wait_for_selector": "#ready",
                    },
                    "enable_adaptive": False,
                    "captures": [
                        {
                            "name": "bootstrap",
                            "type": "embedded_json",
                            "selector": "#page-state",
                            "required": True,
                            "max_bytes": 1024,
                        },
                        {
                            "name": "catalog",
                            "type": "network_json",
                            "url_glob": f"{root}/api/catalog*",
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

            records = await spider.run()

            self.assertEqual(records[0]["title"], "Network title")
            self.assertEqual(records[0]["score"], 9)
            self.assertIn(("GET", "/api/catalog"), server.seen_requests)

    async def test_required_capture_failure_returns_no_partial_records(self) -> None:
        with _fixture_server() as server:
            root = f"http://127.0.0.1:{server.port}"
            spider = GenericSpider(
                {
                    "start_url": f"{root}/crawler-missing-capture",
                    "browser": _browser_config(),
                    "request": {"timeout_ms": 500, "wait_until": "load"},
                    "enable_adaptive": False,
                    "captures": [
                        {
                            "name": "catalog",
                            "type": "network_json",
                            "url_glob": f"{root}/api/never*",
                            "required": True,
                            "max_bytes": 1024,
                        }
                    ],
                    "fields": [{"name": "title", "source": "catalog.title"}],
                }
            )

            with self.assertRaisesRegex(RequiredCaptureError, "capture timed out after 500ms"):
                await spider.run()
            self.assertEqual(spider.results, [])


if __name__ == "__main__":
    unittest.main()

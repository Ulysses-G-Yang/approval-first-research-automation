from __future__ import annotations

import os
import threading
import unittest
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Iterator
from unittest.mock import patch
from urllib.parse import urlparse

import yaml

from research_assistant.models import TaskSpec
from research_assistant.registry import ToolContext, ToolError
from research_assistant.tools import ApprovedCrawlerSpec, BrowserExtractTool
from research_assistant.workspace import TaskWorkspace
_RUN_REAL_CHROMIUM = os.environ.get("CRAWLER_BROWSER_INTEGRATION") == "1"
_CHROMIUM_EXECUTABLE = os.environ.get("CRAWLER_CHROMIUM_EXECUTABLE")


def _browser_config() -> dict[str, object]:
    config: dict[str, object] = {"headless": True, "stealth": False}
    if _CHROMIUM_EXECUTABLE:
        config["launch"] = {"executable_path": _CHROMIUM_EXECUTABLE}
    return config


class _FixtureServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self) -> None:
        super().__init__(("127.0.0.1", 0), _FixtureHandler)
        self.seen_requests: list[tuple[str, str]] = []
        self._seen_lock = threading.Lock()

    @property
    def port(self) -> int:
        return int(self.server_address[1])

    def record(self, method: str, path: str) -> None:
        with self._seen_lock:
            self.seen_requests.append((method, path))


class _FixtureHandler(BaseHTTPRequestHandler):
    server: _FixtureServer

    def log_message(self, _format: str, *_args: object) -> None:
        return None

    def _send(
        self,
        status: int,
        body: str | bytes = b"",
        *,
        content_type: str = "text/html; charset=utf-8",
        headers: dict[str, str] | None = None,
    ) -> None:
        payload = body.encode("utf-8") if isinstance(body, str) else body
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(payload)))
        for name, value in (headers or {}).items():
            self.send_header(name, value)
        self.end_headers()
        if payload:
            self.wfile.write(payload)

    def do_POST(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        self.server.record("POST", self.path)
        self._send(204, b"")

    def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        self.server.record("GET", self.path)
        port = self.server.port

        if self.path == "/ok":
            self._send(200, "<h1 id='title'>fixture-ok</h1><img src='/pixel.png'>")
            return
        if self.path == "/same-host-redirect":
            self._send(302, headers={"Location": "/ok"})
            return
        if self.path == "/cross-host-redirect":
            self._send(302, headers={"Location": f"http://localhost:{port}/ok"})
            return
        if self.path == "/unapproved-subresource":
            self._send(
                200,
                f"<h1 id='title'>partial</h1><img src='http://localhost:{port}/pixel.png'>",
            )
            return
        if self.path == "/private-subresource":
            self._send(
                200,
                f"<h1 id='title'>partial</h1><img src='http://127.0.0.2:{port}/pixel.png'>",
            )
            return
        if self.path == "/post":
            self._send(200, "post endpoint")
            return
        if self.path == "/post-page":
            self._send(
                200,
                """
                <h1 id='title'>partial</h1>
                <script>
                  fetch('/post', {method: 'POST', body: 'blocked'})
                    .catch(() => {})
                    .finally(() => document.body.insertAdjacentHTML('beforeend', '<div id="done">done</div>'));
                </script>
                """,
            )
            return
        if self.path == "/websocket-page":
            self._send(
                200,
                f"""
                <h1 id='title'>partial</h1>
                <script>
                  const socket = new WebSocket('ws://127.0.0.1:{port}/socket');
                  const done = () => {{
                    if (!document.querySelector('#done')) {{
                      document.body.insertAdjacentHTML('beforeend', '<div id="done">done</div>');
                    }}
                  }};
                  socket.addEventListener('error', done);
                  socket.addEventListener('close', done);
                  setTimeout(done, 250);
                </script>
                """,
            )
            return
        if self.path == "/service-worker-page":
            self._send(
                200,
                """
                <body>
                <script>
                  let finished = false;
                  const finish = status => {
                    if (finished) return;
                    finished = true;
                    document.body.insertAdjacentHTML(
                      'beforeend', `<span id="sw-status">${status}</span>`
                    );
                  };
                  setTimeout(() => finish('blocked'), 500);
                  try {
                    navigator.serviceWorker.register('/sw.js')
                      .then(registration => setTimeout(
                        () => finish(
                          registration.active || registration.installing || registration.waiting
                            ? 'active'
                            : 'blocked'
                        ),
                        100
                      ))
                      .catch(() => finish('blocked'));
                  } catch (_) {
                    finish('blocked');
                  }
                </script>
                </body>
                """,
            )
            return
        if self.path == "/sw.js":
            self._send(200, "self.addEventListener('fetch', () => {});", content_type="text/javascript")
            return
        if self.path == "/many":
            self._send(
                200,
                "<h1 id='title'>partial</h1>"
                "<img src='/pixel.png?1'><img src='/pixel.png?2'><img src='/pixel.png?3'>",
            )
            return
        if self.path == "/never-ready":
            self._send(200, "<h1 id='title'>loaded-but-never-ready</h1>")
            return
        if self.path == "/crawler-captures":
            self._send(
                200,
                """
                <script id="page-state" type="application/json">{"props":{"score":9}}</script>
                <script>
                  fetch('/api/catalog')
                    .then(response => response.json())
                    .then(() => document.body.insertAdjacentHTML('beforeend', '<div id="ready">ready</div>'));
                </script>
                """,
            )
            return
        if self.path == "/crawler-missing-capture":
            self._send(200, "<h1>no matching JSON response</h1>")
            return
        if self.path == "/api/catalog":
            self._send(
                200,
                '{"payload":{"items":[{"title":"Network title"}]}}',
                content_type="application/json",
            )
            return
        if self.path.startswith("/pixel.png"):
            self._send(200, b"pixel", content_type="image/png")
            return
        if self.path == "/socket":
            self._send(400, "WebSocket handshake must be blocked before the fixture sees it.")
            return
        self._send(404, "not found")


@contextmanager
def _fixture_server() -> Iterator[_FixtureServer]:
    server = _FixtureServer()
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def _test_loopback_validator(fixture_port: int):
    """Allow only this test server while retaining host and private-IP checks.

    Production validation is intentionally not changed: the integration suite injects
    this validator because the real policy correctly rejects loopback and random ports.
    """

    async def validate(url: str, *, approved_hosts=None, timeout_seconds: float = 5.0) -> str:
        del timeout_seconds
        parsed = urlparse(url)
        host = (parsed.hostname or "").rstrip(".").lower()
        try:
            port = parsed.port
        except ValueError as exc:
            raise ToolError("URL contains an invalid port.") from exc
        if parsed.scheme not in {"http", "https"} or not host or port != fixture_port:
            raise ToolError("Test fixture validator rejected a non-fixture URL.")
        normalized_approved = {
            str(value).rstrip(".").lower() for value in (approved_hosts or ())
        }
        if approved_hosts is not None and host not in normalized_approved:
            raise ToolError(f"Network target host is not approved: {host}")
        if host == "127.0.0.2":
            raise ToolError("URL resolves to a non-public network address.")
        if host not in {"127.0.0.1", "localhost"}:
            raise ToolError("Test fixture validator rejected a non-fixture host.")
        return parsed.geturl()

    return validate


@unittest.skipUnless(
    _RUN_REAL_CHROMIUM,
    "set CRAWLER_BROWSER_INTEGRATION=1 after installing Playwright Chromium",
)
class BrowserNetworkChromiumIntegrationTests(unittest.IsolatedAsyncioTestCase):
    def _make_tool_run(
        self,
        root: Path,
        *,
        url: str,
        approved_hosts: frozenset[str] = frozenset({"127.0.0.1"}),
        max_requests: int = 20,
        max_duration_seconds: float = 15,
        wait_for_selector: str | None = None,
        field_selector: str = "#title",
    ) -> tuple[BrowserExtractTool, ToolContext, dict[str, str], ApprovedCrawlerSpec]:
        config_path = root / "crawler.yaml"
        raw_config = {
            "start_url": url,
            "browser": _browser_config(),
            "request": {"timeout_ms": 5_000, "wait_until": "load"},
            "enable_adaptive": False,
            "fields": [{"name": "value", "selector": field_selector}],
        }
        if wait_for_selector:
            raw_config["request"]["wait_for_selector"] = wait_for_selector
        config_path.write_text(yaml.safe_dump(raw_config, sort_keys=False), encoding="utf-8")
        task = TaskSpec.create("crawl fixture", "crawler_report", None, [], [str(config_path)])
        workspace = TaskWorkspace.create(root / "tasks", task)
        context = ToolContext(task=task, workspace=workspace)
        spec = ApprovedCrawlerSpec(
            config=raw_config,
            start_urls=(url,),
            approved_hosts=approved_hosts,
            max_requests=max_requests,
            max_duration_seconds=max_duration_seconds,
        )
        return BrowserExtractTool(), context, {"config_path": str(config_path)}, spec

    async def _run_fixture(
        self,
        server: _FixtureServer,
        path: str,
        **kwargs,
    ):
        temp = TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        root = Path(temp.name)
        url = f"http://127.0.0.1:{server.port}{path}"
        tool, context, arguments, spec = self._make_tool_run(root, url=url, **kwargs)
        with patch.object(ApprovedCrawlerSpec, "from_mapping", return_value=spec), patch(
            "research_assistant.tools.validate_public_url_async",
            new=_test_loopback_validator(server.port),
        ):
            result = await tool.run(context, arguments)
        return context.workspace, result

    async def _assert_blocked_without_artifact(
        self,
        server: _FixtureServer,
        path: str,
        *,
        error_text: str,
        **kwargs,
    ) -> TaskWorkspace:
        temp = TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        root = Path(temp.name)
        url = f"http://127.0.0.1:{server.port}{path}"
        tool, context, arguments, spec = self._make_tool_run(root, url=url, **kwargs)
        with patch.object(ApprovedCrawlerSpec, "from_mapping", return_value=spec), patch(
            "research_assistant.tools.validate_public_url_async",
            new=_test_loopback_validator(server.port),
        ):
            with self.assertRaisesRegex(ToolError, error_text):
                await tool.run(context, arguments)
        self.assertEqual(context.workspace.list_artifacts("crawler_records"), [])
        self.assertEqual(list(context.workspace.artifacts_dir.iterdir()), [])
        return context.workspace

    async def test_allowed_same_host_page_redirect_and_subresource_succeed(self) -> None:
        with _fixture_server() as server:
            for path in ("/ok", "/same-host-redirect"):
                with self.subTest(path=path):
                    workspace, result = await self._run_fixture(server, path)
                    self.assertEqual(result.details["record_count"], 1)
                    records = workspace.read_artifact_json(result.artifacts[0])
                    self.assertEqual(records[0]["value"], "fixture-ok")
            self.assertIn(("GET", "/pixel.png"), server.seen_requests)

    async def test_unapproved_host_private_address_and_cross_host_redirect_are_blocked(self) -> None:
        with _fixture_server() as server:
            await self._assert_blocked_without_artifact(
                server,
                "/unapproved-subresource",
                error_text="not approved: localhost",
            )
            await self._assert_blocked_without_artifact(
                server,
                "/private-subresource",
                error_text="non-public network address",
                approved_hosts=frozenset({"127.0.0.1", "127.0.0.2"}),
            )
            await self._assert_blocked_without_artifact(
                server,
                "/cross-host-redirect",
                error_text="not approved: localhost",
            )

    async def test_post_and_websocket_are_blocked_without_partial_artifacts(self) -> None:
        with _fixture_server() as server:
            await self._assert_blocked_without_artifact(
                server,
                "/post-page",
                error_text="method is not allowed: POST",
                wait_for_selector="#done",
            )
            self.assertNotIn(("POST", "/post"), server.seen_requests)

            await self._assert_blocked_without_artifact(
                server,
                "/websocket-page",
                error_text="WebSockets are disabled|WebSocket requests are not allowed",
                wait_for_selector="#done",
            )

    async def test_service_worker_is_disabled_in_real_context(self) -> None:
        with _fixture_server() as server:
            workspace, result = await self._run_fixture(
                server,
                "/service-worker-page",
                wait_for_selector="#sw-status",
                field_selector="#sw-status",
            )
            records = workspace.read_artifact_json(result.artifacts[0])
            self.assertEqual(records[0]["value"], "blocked")
            self.assertNotIn(("GET", "/sw.js"), server.seen_requests)

    async def test_request_limit_and_total_timeout_block_without_partial_artifacts(self) -> None:
        with _fixture_server() as server:
            await self._assert_blocked_without_artifact(
                server,
                "/many",
                error_text="request limit exceeded",
                max_requests=2,
            )
            await self._assert_blocked_without_artifact(
                server,
                "/never-ready",
                error_text="exceeded the 0.2 second limit",
                max_duration_seconds=0.2,
                wait_for_selector="#never-created",
            )


if __name__ == "__main__":
    unittest.main()

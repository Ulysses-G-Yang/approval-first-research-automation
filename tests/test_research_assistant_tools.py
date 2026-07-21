from __future__ import annotations

import time
import unittest
from copy import deepcopy
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import yaml

from research_assistant.models import TaskSpec
from research_assistant.registry import ToolContext, ToolError, ToolPermissionError
from research_assistant.runner import preview_approval
from research_assistant.tools import (
    ApprovedCrawlerSpec,
    BrowserExtractTool,
    BrowserRequestPolicy,
    FileReadTool,
    UrlListReadTool,
    WebFetchTool,
    _http_get,
    validate_public_url,
    validate_public_url_async,
)
from research_assistant.tools import build_default_registry
from research_assistant.workflows import build_workflow_plan, make_model_step
from research_assistant.workspace import TaskWorkspace


class ResearchAssistantToolTests(unittest.IsolatedAsyncioTestCase):
    async def test_file_tool_only_reads_explicit_input(self) -> None:
        with TemporaryDirectory() as temp:
            root = Path(temp)
            allowed = root / "allowed.txt"
            forbidden = root / "forbidden.txt"
            allowed.write_text("allowed", encoding="utf-8")
            forbidden.write_text("forbidden", encoding="utf-8")
            task = TaskSpec.create("read", "file_report", None, [], [str(allowed)])
            workspace = TaskWorkspace.create(root / "tasks", task)
            context = ToolContext(task=task, workspace=workspace)

            result = await FileReadTool().run(context, {"path": str(allowed)})
            self.assertEqual(len(result.artifacts), 1)
            with self.assertRaises(ToolPermissionError):
                await FileReadTool().run(context, {"path": str(forbidden)})

    async def test_web_fetch_normalizes_html_without_network(self) -> None:
        with TemporaryDirectory() as temp:
            root = Path(temp)
            task = TaskSpec.create("web", "research_report", None, ["https://example.com/article"], [])
            workspace = TaskWorkspace.create(root / "tasks", task)
            context = ToolContext(task=task, workspace=workspace)
            response = (
                "https://example.com/article",
                {"content-type": "text/html"},
                200,
                "<html><title>Example</title><body><h1>Hello</h1><script>bad()</script></body></html>",
            )
            with patch("research_assistant.tools.validate_public_url", side_effect=lambda url: url), patch(
                "research_assistant.tools._http_get", new=AsyncMock(return_value=response)
            ):
                result = await WebFetchTool().run(context, {"url": "https://example.com/article"})
            source = workspace.read_artifact_json(result.artifacts[0])
            self.assertEqual(source["title"], "Example")
            self.assertIn("Hello", source["text"])
            self.assertNotIn("bad()", source["text"])

    async def test_url_list_reader_keeps_only_public_unique_candidates(self) -> None:
        with TemporaryDirectory() as temp:
            root = Path(temp)
            links = root / "links.txt"
            links.write_text(
                "https://example.com/a\nhttps://example.com/a\nhttp://localhost:8080\nhttps://example.org/b.",
                encoding="utf-8",
            )
            task = TaskSpec.create("urls", "auto", None, [], [str(links)])
            workspace = TaskWorkspace.create(root / "tasks", task)
            result = await UrlListReadTool().run(ToolContext(task=task, workspace=workspace), {"path": str(links)})
            value = workspace.read_artifact_json(result.artifacts[0])
            self.assertEqual(value["urls"], ["https://example.com/a", "https://example.org/b"])

    async def test_browser_tool_rejects_actions_before_launching(self) -> None:
        with TemporaryDirectory() as temp:
            root = Path(temp)
            config = root / "unsafe.yaml"
            config.write_text("start_url: https://example.com\nactions:\n  - type: evaluate\n", encoding="utf-8")
            task = TaskSpec.create("crawl", "auto", None, [], [str(config)])
            workspace = TaskWorkspace.create(root / "tasks", task)
            with self.assertRaises(ToolError):
                await BrowserExtractTool().run(ToolContext(task=task, workspace=workspace), {"config_path": str(config)})

    def test_browser_plan_rejects_llm_configuration(self) -> None:
        with TemporaryDirectory() as temp:
            config = Path(temp) / "spider.yaml"
            config.write_text(
                "start_url: https://example.com/list\nllm:\n  enable_repair: true\n  provider: qwen\n  secret_ref: provider:qwen\n",
                encoding="utf-8",
            )
            task = TaskSpec.create("crawl", "auto", None, [], [str(config)])
            with self.assertRaises(ToolError):
                make_model_step(
                    task,
                    build_default_registry(),
                    1,
                    "crawl declared source",
                    "browser.extract",
                    {"config_path": "input:0"},
                )

    def test_crawler_workflow_registers_existing_spider_behind_approval(self) -> None:
        with TemporaryDirectory() as temp:
            config = Path(temp) / "spider.yaml"
            config.write_text("start_url: https://example.com/list\nfields:\n  - name: title\n    selector: h1\n", encoding="utf-8")
            task = TaskSpec.create("crawl", "crawler_report", None, [], [str(config)])
            plan = build_workflow_plan(task, build_default_registry())
            self.assertEqual([step.call.tool_name for step in plan.steps], ["browser.extract", "data.normalize", "report.compose"])

    def test_private_network_targets_are_rejected(self) -> None:
        for url in ("http://127.0.0.1", "http://localhost", "http://10.0.0.1", "http://[::1]"):
            with self.assertRaises(ToolError):
                validate_public_url(url)

    def test_nonstandard_network_ports_are_rejected(self) -> None:
        for url in ("https://example.com:444/", "http://example.com:8080/"):
            with self.subTest(url=url), self.assertRaises(ToolError):
                validate_public_url(url)
            with self.subTest(config=url), self.assertRaises(ToolError):
                ApprovedCrawlerSpec.from_mapping({"start_url": url, "fields": []})

    async def test_dns_validation_timeout_does_not_block_event_loop(self) -> None:
        def slow_validation(url: str, **_kwargs) -> str:
            time.sleep(0.15)
            return url

        started = time.monotonic()
        with patch("research_assistant.tools.validate_public_url", side_effect=slow_validation):
            with self.assertRaises(ToolError):
                await validate_public_url_async("https://example.com", timeout_seconds=0.01)
        self.assertLess(time.monotonic() - started, 0.1)

    def test_approved_crawler_spec_rejects_dangerous_configuration(self) -> None:
        cases = [
            {"actions": [{"type": "evaluate"}]},
            {"llm": {"enable_repair": True}},
            {"browser": {"cdp_url": "http://127.0.0.1:9222"}},
            {"browser": {"launch": {"executable_path": "tool.exe"}}},
            {"browser": {"context": {"storage_state": "profile.json"}}},
            {"browser": {"proxy": {"server": "http://127.0.0.1:8080"}}},
        ]
        for extra in cases:
            config = {"start_url": "https://example.com", "fields": []}
            config.update(deepcopy(extra))
            with self.subTest(extra=extra), self.assertRaises(ToolError):
                ApprovedCrawlerSpec.from_mapping(config)

    def test_approved_crawler_spec_collects_explicit_hosts_and_limits(self) -> None:
        spec = ApprovedCrawlerSpec.from_mapping(
            {
                "start_url": "https://example.com/items",
                "browser": {"headless": True, "stealth": False},
                "fields": [{"name": "title", "selector": "h1"}],
                "network": {
                    "allowed_hosts": ["cdn.example.com"],
                    "max_requests": 25,
                    "max_duration_seconds": 30,
                },
            }
        )
        self.assertEqual(spec.approved_hosts, frozenset({"example.com", "cdn.example.com"}))
        self.assertEqual(spec.max_requests, 25)
        self.assertEqual(spec.max_duration_seconds, 30)
        self.assertNotIn("network", spec.config)

    def test_approved_browser_template_matches_strict_schema(self) -> None:
        path = Path(__file__).resolve().parents[1] / "configs" / "approved_browser_template.yaml"
        spec = ApprovedCrawlerSpec.from_mapping(yaml.safe_load(path.read_text(encoding="utf-8")))
        self.assertEqual(spec.start_urls, ("https://example.com/",))
        self.assertEqual(spec.approved_hosts, frozenset({"example.com"}))

    def test_crawler_approval_binds_normalized_safe_configuration(self) -> None:
        with TemporaryDirectory() as temp:
            root = Path(temp)
            config = root / "crawler.yaml"
            config.write_text(
                "start_url: https://example.com/items\n"
                "fields:\n  - name: title\n    selector: h1\n"
                "network:\n  allowed_hosts: [cdn.example.com]\n  max_requests: 25\n",
                encoding="utf-8",
            )
            task = TaskSpec.create("crawl", "crawler_report", None, [], [str(config)])
            workspace = TaskWorkspace.create(root / "tasks", task)
            workspace.save_plan(build_workflow_plan(task, build_default_registry()))

            _, manifest, _ = preview_approval(workspace, "step-01")

            approved = manifest["tool_input"]["approved_crawler"]
            self.assertEqual(approved["approved_hosts"], ["cdn.example.com", "example.com"])
            self.assertEqual(approved["max_requests"], 25)
            self.assertNotIn("network", approved["config"])

    async def test_browser_policy_allows_only_approved_public_gets(self) -> None:
        class FakeRoute:
            def __init__(self, url: str, method: str = "GET", resource_type: str = "document"):
                self.request = SimpleNamespace(url=url, method=method, resource_type=resource_type)
                self.aborted = False
                self.continued = False

            async def abort(self, _reason: str) -> None:
                self.aborted = True

            async def continue_(self) -> None:
                self.continued = True

        policy = BrowserRequestPolicy(frozenset({"93.184.216.34"}), max_requests=2)
        allowed = FakeRoute("https://93.184.216.34/page")
        await policy._handle_route(allowed)
        self.assertTrue(allowed.continued)

        post = FakeRoute("https://93.184.216.34/api", method="POST")
        await policy._handle_route(post)
        self.assertTrue(post.aborted)

        over_limit = FakeRoute("https://93.184.216.34/next")
        await policy._handle_route(over_limit)
        self.assertTrue(over_limit.aborted)
        with self.assertRaises(ToolError):
            policy.raise_if_violated()

    async def test_browser_policy_blocks_private_unapproved_and_websocket_requests(self) -> None:
        class FakeRoute:
            def __init__(self, url: str, resource_type: str = "document"):
                self.request = SimpleNamespace(url=url, method="GET", resource_type=resource_type)
                self.aborted = False

            async def abort(self, _reason: str) -> None:
                self.aborted = True

            async def continue_(self) -> None:
                raise AssertionError("blocked request continued")

        policy = BrowserRequestPolicy(
            frozenset({"127.0.0.1", "93.184.216.34"}),
            max_requests=10,
        )
        private = FakeRoute("http://127.0.0.1/secret")
        await policy._handle_route(private)
        self.assertTrue(private.aborted)

        unapproved = FakeRoute("https://93.184.216.35/other")
        await policy._handle_route(unapproved)
        self.assertTrue(unapproved.aborted)

        websocket = FakeRoute("https://93.184.216.34/socket", resource_type="websocket")
        await policy._handle_route(websocket)
        self.assertTrue(websocket.aborted)

    async def test_browser_policy_installs_routes_and_disables_service_workers(self) -> None:
        class FakeContext:
            def __init__(self):
                self.http_handler = None
                self.websocket_handler = None
                self.init_script = None

            async def route(self, pattern, handler) -> None:
                self.http_handler = (pattern, handler)

            async def route_web_socket(self, pattern, handler) -> None:
                self.websocket_handler = (pattern, handler)

            async def add_init_script(self, script) -> None:
                self.init_script = script

        policy = BrowserRequestPolicy(frozenset({"example.com"}), max_requests=10)
        context = FakeContext()
        await policy.install(context)

        self.assertEqual(
            policy.context_options,
            {"accept_downloads": False, "service_workers": "block"},
        )
        self.assertEqual(context.http_handler[0], "**/*")
        self.assertEqual(context.websocket_handler[0], "**/*")
        self.assertIn("RTCPeerConnection", context.init_script)
        self.assertIn("--disable-quic", policy.launch_options["args"])
        self.assertIn(
            "--force-webrtc-ip-handling-policy=disable_non_proxied_udp",
            policy.launch_options["args"],
        )

        socket_route = SimpleNamespace(close=AsyncMock())
        await context.websocket_handler[1](socket_route)
        socket_route.close.assert_awaited_once()
        with self.assertRaises(ToolError):
            policy.raise_if_violated()

    async def test_browser_tool_fails_instead_of_returning_partial_policy_results(self) -> None:
        class ViolatingSpider:
            def __init__(self, _config, network_policy):
                self.policy = network_policy

            async def run(self):
                self.policy._record_violation("Browser request limit exceeded.")
                return [{"partial": True}]

        with TemporaryDirectory() as temp:
            root = Path(temp)
            config = root / "crawler.yaml"
            config.write_text(
                "start_url: https://example.com/\nfields:\n  - name: title\n    selector: h1\n",
                encoding="utf-8",
            )
            task = TaskSpec.create("crawl", "crawler_report", None, [], [str(config)])
            workspace = TaskWorkspace.create(root / "tasks", task)
            context = ToolContext(task=task, workspace=workspace)
            with patch(
                "research_assistant.tools.validate_public_url_async",
                new=AsyncMock(side_effect=lambda url, **_kwargs: url),
            ), patch("core.spider_engine.GenericSpider", ViolatingSpider):
                with self.assertRaises(ToolError):
                    await BrowserExtractTool().run(
                        context,
                        {"config_path": task.input_files[0]},
                    )
            self.assertEqual(workspace.list_artifacts("crawler_records"), [])

    async def test_http_client_ignores_environment_proxy_and_rejects_cross_host_redirect(self) -> None:
        seen: dict = {}

        class FakeResponse:
            status_code = 302
            headers = {"location": "https://other.example/next"}

            async def aclose(self) -> None:
                return None

        class FakeClient:
            def __init__(self, **kwargs):
                seen.update(kwargs)

            async def __aenter__(self):
                return self

            async def __aexit__(self, *_args):
                return None

            def build_request(self, _method: str, url: str):
                return url

            async def send(self, _request, stream: bool = False):
                return FakeResponse()

        def fake_validate(url: str, _resolver=None, *, approved_hosts=None) -> str:
            host = url.split("/", 3)[2]
            if approved_hosts is not None and host not in approved_hosts:
                raise ToolError(f"Network target host is not approved: {host}")
            return url

        with patch("httpx.AsyncClient", FakeClient), patch(
            "research_assistant.tools.validate_public_url", side_effect=fake_validate
        ):
            with self.assertRaises(ToolError):
                await _http_get("https://example.com/start", 1, 1024)
        self.assertIs(seen["trust_env"], False)

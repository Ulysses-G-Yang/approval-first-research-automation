from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import AsyncMock, patch

from research_assistant.models import TaskSpec
from research_assistant.registry import ToolContext, ToolError, ToolPermissionError
from research_assistant.models import RiskLevel
from research_assistant.tools import BrowserExtractTool, FileReadTool, UrlListReadTool, WebFetchTool, validate_public_url
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

    def test_browser_plan_exposes_llm_repair_as_sensitive(self) -> None:
        with TemporaryDirectory() as temp:
            config = Path(temp) / "spider.yaml"
            config.write_text(
                "start_url: https://example.com/list\nllm:\n  enable_repair: true\n  provider: qwen\n  secret_ref: provider:qwen\n",
                encoding="utf-8",
            )
            task = TaskSpec.create("crawl", "auto", None, [], [str(config)])
            step = make_model_step(
                task,
                build_default_registry(),
                1,
                "crawl declared source",
                "browser.extract",
                {"config_path": "input:0"},
            )
            self.assertEqual(step.call.risk, RiskLevel.SENSITIVE)
            self.assertIn("example.com", step.call.target)
            self.assertIn("qwen", step.call.target)

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

from __future__ import annotations

import unittest
from pathlib import Path

from research_assistant.models import TaskSpec
from research_assistant.planner import AgentPlanner, PlanningError
from research_assistant.providers import StaticProvider
from research_assistant.tools import build_default_registry


class ResearchAssistantPlannerTests(unittest.IsolatedAsyncioTestCase):
    async def test_model_plan_uses_input_reference_without_sending_full_path(self) -> None:
        task = TaskSpec.create(
            "summarize sales",
            "auto",
            "demo",
            [],
            [str(Path("C:/private-folder/sales.csv"))],
        )
        provider = StaticProvider(
            [
                """{
                  "summary": "读取数据并生成报告",
                  "steps": [
                    {"tool_name": "file.read", "arguments": {"path": "input:0"}, "description": "读取 CSV"},
                    {"tool_name": "data.normalize", "arguments": {}, "description": "去重"},
                    {"tool_name": "report.compose", "arguments": {}, "description": "报告"}
                  ]
                }"""
            ]
        )
        planner = AgentPlanner(provider, build_default_registry())
        plan = await planner.create_plan(task)

        self.assertEqual(plan.steps[0].call.arguments["path"], str(Path("C:/private-folder/sales.csv")))
        self.assertEqual(plan.steps[-1].call.tool_name, "report.compose")
        sent_prompt = provider.requests[0][1]
        self.assertIn('"ref": "input:0"', sent_prompt)
        self.assertNotIn("C:/private-folder/sales.csv", sent_prompt)

    async def test_model_cannot_use_unregistered_tools(self) -> None:
        task = TaskSpec.create("unsafe", "auto", "demo", [], [])
        provider = StaticProvider(
            ['{"summary": "bad", "steps": [{"tool_name": "shell.exec", "arguments": {}, "description": "bad"}]}']
        )
        with self.assertRaises(PlanningError):
            await AgentPlanner(provider, build_default_registry()).create_plan(task)

    async def test_model_cannot_read_arbitrary_file_path(self) -> None:
        task = TaskSpec.create("unsafe", "auto", "demo", [], ["C:/data/input.csv"])
        provider = StaticProvider(
            [
                '{"summary": "bad", "steps": [{"tool_name": "file.read", "arguments": {"path": "C:/Windows/system.ini"}, "description": "bad"}]}'
            ]
        )
        with self.assertRaises(PlanningError):
            await AgentPlanner(provider, build_default_registry()).create_plan(task)

    async def test_model_cannot_fetch_an_unlisted_url(self) -> None:
        task = TaskSpec.create("unsafe", "auto", "demo", ["https://example.com/allowed"], [])
        provider = StaticProvider(
            [
                '{"summary": "bad", "steps": [{"tool_name": "web.fetch", "arguments": {"url": "https://example.com/unlisted"}, "description": "bad"}]}'
            ]
        )
        with self.assertRaises(PlanningError):
            await AgentPlanner(provider, build_default_registry()).create_plan(task)

    async def test_invalid_json_is_rejected(self) -> None:
        task = TaskSpec.create("invalid", "auto", "demo", [], [])
        provider = StaticProvider(["not json"])
        with self.assertRaises(PlanningError):
            await AgentPlanner(provider, build_default_registry()).create_plan(task)

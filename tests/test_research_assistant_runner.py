from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from research_assistant.models import TaskSpec, TaskStatus
from research_assistant.providers import StaticProvider
from research_assistant.runner import ApprovalError, TaskRunner, approve_step
from research_assistant.tools import build_default_registry
from research_assistant.workflows import build_workflow_plan
from research_assistant.workspace import TaskWorkspace


class ResearchAssistantRunnerTests(unittest.IsolatedAsyncioTestCase):
    async def test_unapproved_steps_do_not_execute_then_report_is_traceable(self) -> None:
        with TemporaryDirectory() as temp:
            temp_root = Path(temp)
            source = temp_root / "sales.csv"
            source.write_text("name,amount\nA,10\nA,10\nB,20\n", encoding="utf-8")
            task = TaskSpec.create("汇总销售数据", "file_report", None, [], [str(source)])
            workspace = TaskWorkspace.create(temp_root / "tasks", task)
            plan = build_workflow_plan(task, build_default_registry())
            workspace.save_plan(plan)
            task.status = TaskStatus.WAITING_APPROVAL
            workspace.save_task(task)
            runner = TaskRunner(build_default_registry())

            waiting = await runner.resume(workspace)
            self.assertEqual(waiting.task_status, TaskStatus.WAITING_APPROVAL)
            self.assertEqual(workspace.list_artifacts(), [])
            with self.assertRaises(ApprovalError):
                approve_step(workspace, "step-02")

            for step_id in ("step-01", "step-02", "step-03"):
                approve_step(workspace, step_id)
                result = await runner.resume(workspace)
                self.assertNotEqual(result.task_status, TaskStatus.FAILED)

            self.assertEqual(workspace.load_task().status, TaskStatus.COMPLETED)
            report = workspace.list_artifacts("report")[0]
            sources = workspace.list_artifacts("sources_manifest")[0]
            self.assertIn("汇总销售数据", workspace.read_artifact_text(report))
            self.assertIn("sales.csv", workspace.read_artifact_text(sources))
            self.assertEqual(len(workspace.list_artifacts("dataset")), 1)

    async def test_model_summary_is_separate_and_degrades_without_stopping_report(self) -> None:
        with TemporaryDirectory() as temp:
            temp_root = Path(temp)
            source = temp_root / "notes.txt"
            source.write_text("Approved local note", encoding="utf-8")
            task = TaskSpec.create("汇总笔记", "file_report", "demo", [], [str(source)])
            workspace = TaskWorkspace.create(temp_root / "tasks", task)
            plan = build_workflow_plan(task, build_default_registry())
            workspace.save_plan(plan)
            task.status = TaskStatus.WAITING_APPROVAL
            workspace.save_task(task)
            runner = TaskRunner(build_default_registry(), provider=StaticProvider([]))

            for step in plan.steps:
                approve_step(workspace, step.id)
                result = await runner.resume(workspace)
                self.assertNotEqual(result.task_status, TaskStatus.FAILED)

            summary = workspace.list_artifacts("model_summary")[0]
            report = workspace.list_artifacts("report")[0]
            self.assertEqual(workspace.read_artifact_text(summary), "")
            self.assertIn("模型摘要未启用或不可用", workspace.read_artifact_text(report))
            self.assertEqual(workspace.load_task().status, TaskStatus.COMPLETED)

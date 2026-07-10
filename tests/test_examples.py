from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from research_assistant.models import TaskSpec, TaskStatus
from research_assistant.runner import TaskRunner, approve_step
from research_assistant.tools import build_default_registry
from research_assistant.workflows import build_workflow_plan
from research_assistant.workspace import TaskWorkspace


ROOT = Path(__file__).resolve().parents[1]


async def _run_all_steps(workspace: TaskWorkspace, plan) -> None:
    runner = TaskRunner(build_default_registry())
    for step in plan.steps:
        approve_step(workspace, step.id)
        result = await runner.resume(workspace)
        if result.task_status == TaskStatus.FAILED:
            raise AssertionError(result.message)


class ExampleWorkflowTests(unittest.IsolatedAsyncioTestCase):
    async def test_research_report_example_is_reproducible_offline(self) -> None:
        source = ROOT / "examples" / "research-report" / "market-notes.csv"
        with TemporaryDirectory() as temp:
            task = TaskSpec.create("Summarize market notes", "file_report", None, [], [str(source)])
            workspace = TaskWorkspace.create(Path(temp) / "tasks", task)
            plan = build_workflow_plan(task, build_default_registry())
            workspace.save_plan(plan)
            task.status = TaskStatus.WAITING_APPROVAL
            workspace.save_task(task)
            await _run_all_steps(workspace, plan)
            report = workspace.read_artifact_text(workspace.list_artifacts("report")[0])
            dataset = workspace.read_artifact_text(workspace.list_artifacts("dataset")[0])
        self.assertIn("market-notes.csv", report)
        self.assertIn("public-demo-a", dataset)

    async def test_document_and_offline_draft_examples_copy_local_asset(self) -> None:
        source = ROOT / "examples" / "content-draft" / "source" / "article.md"
        with TemporaryDirectory() as temp:
            task = TaskSpec.create(
                "Prepare local draft",
                "content_save_draft",
                None,
                [],
                [str(source)],
                options={"platform": "juejin"},
            )
            workspace = TaskWorkspace.create(Path(temp) / "tasks", task)
            plan = build_workflow_plan(task, build_default_registry())
            workspace.save_plan(plan)
            task.status = TaskStatus.WAITING_APPROVAL
            workspace.save_task(task)
            await _run_all_steps(workspace, plan)
            manifest = workspace.read_artifact_json(workspace.list_artifacts("draft_manifest")[0])
            draft = workspace.read_artifact_text(workspace.list_artifacts("draft_markdown")[0])
        self.assertFalse(manifest["network_access"])
        self.assertFalse(manifest["published"])
        self.assertIn("assets/001-image-001.svg", draft)

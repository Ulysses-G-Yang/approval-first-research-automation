from __future__ import annotations

import multiprocessing
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from research_assistant.cli import main
from research_assistant.models import Artifact, StepStatus, TaskSpec, TaskStatus
from research_assistant.runner import ApprovalError, TaskRunner, approve_step, preview_approval, recover_step
from research_assistant.tools import build_default_registry
from research_assistant.workflows import build_workflow_plan
from research_assistant.workspace import TaskBusyError, TaskWorkspace, WorkspaceError


def _try_task_lock(root: str, task_id: str, queue) -> None:
    workspace = TaskWorkspace.open(Path(root), task_id)
    try:
        with workspace.lock():
            queue.put("acquired")
    except TaskBusyError:
        queue.put("busy")


class ExecutionIntegrityTests(unittest.IsolatedAsyncioTestCase):
    def _workspace(self, root: Path, source: Path) -> tuple[TaskWorkspace, TaskSpec]:
        task = TaskSpec.create("summarize", "file_report", None, [], [str(source)])
        workspace = TaskWorkspace.create(root, task)
        plan = build_workflow_plan(task, build_default_registry())
        workspace.save_plan(plan)
        task.status = TaskStatus.WAITING_APPROVAL
        workspace.save_task(task)
        return workspace, task

    def _committed_attempt(
        self, workspace: TaskWorkspace, task: TaskSpec
    ) -> tuple[str, Artifact]:
        approved = approve_step(workspace, "step-01")
        attempt_id = workspace.begin_attempt("step-01", approved.approval_fingerprint or "")
        workspace.start_attempt(attempt_id)
        artifact = workspace.write_text_artifact(
            "result.md", "durable", kind="report", description="result"
        )
        workspace.end_attempt_context()
        workspace.commit_attempt(attempt_id, "done")
        plan = workspace.load_plan()
        plan.steps[0].status = StepStatus.RUNNING
        plan.steps[0].attempt_id = attempt_id
        task.status = TaskStatus.RUNNING
        workspace.save_plan(plan)
        workspace.save_task(task)
        return attempt_id, artifact

    async def test_original_input_change_does_not_change_approved_snapshot(self) -> None:
        with TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "input.txt"
            source.write_text("approved", encoding="utf-8")
            workspace, _ = self._workspace(root / "tasks", source)
            approve_step(workspace, "step-01")

            source.write_text("changed after approval", encoding="utf-8")
            result = await TaskRunner(build_default_registry()).resume(workspace)

            step = workspace.load_plan().steps[0]
            self.assertEqual(step.status, StepStatus.COMPLETED)
            self.assertNotEqual(result.task_status, TaskStatus.FAILED)
            artifact = workspace.list_artifacts("file_source")[0]
            self.assertIn("approved", workspace.read_artifact_text(artifact))
            self.assertNotIn("changed after approval", workspace.read_artifact_text(artifact))

    def test_snapshot_tamper_or_unregistered_neighbor_blocks_approval(self) -> None:
        for mutation in ("tamper", "neighbor"):
            with self.subTest(mutation=mutation), TemporaryDirectory() as temp:
                root = Path(temp)
                source = root / "input.txt"
                source.write_text("approved", encoding="utf-8")
                workspace, task = self._workspace(root / "tasks", source)
                snapshot = Path(task.input_files[0])
                if mutation == "tamper":
                    snapshot.write_text("changed", encoding="utf-8")
                else:
                    (snapshot.parent / "unapproved.txt").write_text("extra", encoding="utf-8")

                with self.assertRaises(WorkspaceError):
                    preview_approval(workspace, "step-01")

    async def test_provider_or_plugin_environment_change_invalidates_approval(self) -> None:
        with TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "input.txt"
            source.write_text("approved", encoding="utf-8")
            workspace, _ = self._workspace(root / "tasks", source)
            approved_environment = {
                "provider": {"name": "demo", "model": "v1", "endpoint": "https://one.example"},
                "plugins": ["trusted.plugin"],
                "tools": build_default_registry().describe(),
            }
            approve_step(workspace, "step-01", approved_environment)
            changed_environment = {
                **approved_environment,
                "provider": {"name": "demo", "model": "v1", "endpoint": "https://two.example"},
            }

            with self.assertRaises(ApprovalError):
                await TaskRunner(
                    build_default_registry(), execution_environment=changed_environment
                ).resume(workspace)

    async def test_interrupted_running_step_requires_explicit_retry_and_reapproval(self) -> None:
        with TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "input.txt"
            source.write_text("approved", encoding="utf-8")
            workspace, task = self._workspace(root / "tasks", source)
            approved = approve_step(workspace, "step-01")
            attempt_id = workspace.begin_attempt("step-01", approved.approval_fingerprint or "")
            workspace.end_attempt_context()
            plan = workspace.load_plan()
            plan.steps[0].status = StepStatus.RUNNING
            plan.steps[0].attempt_id = attempt_id
            task.status = TaskStatus.RUNNING
            workspace.save_plan(plan)
            workspace.save_task(task)

            result = await TaskRunner(build_default_registry()).resume(workspace)
            self.assertEqual(result.task_status, TaskStatus.RECOVERY_REQUIRED)
            self.assertEqual(workspace.load_plan().steps[0].status, StepStatus.RECOVERY_REQUIRED)

            recovered = recover_step(workspace, "step-01", "retry")
            self.assertEqual(recovered.task_status, TaskStatus.WAITING_APPROVAL)
            step = workspace.load_plan().steps[0]
            self.assertEqual(step.status, StepStatus.PLANNED)
            self.assertIsNone(step.approval_fingerprint)

    async def test_committed_attempt_can_be_finalized_without_reexecution(self) -> None:
        with TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "input.txt"
            source.write_text("approved", encoding="utf-8")
            workspace, task = self._workspace(root / "tasks", source)
            _, artifact = self._committed_attempt(workspace, task)

            await TaskRunner(build_default_registry()).resume(workspace)
            recovered = recover_step(workspace, "step-01", "finalize")
            self.assertEqual(recovered.task_status, TaskStatus.WAITING_APPROVAL)
            self.assertIn(artifact.id, workspace.load_plan().steps[0].artifact_ids)
            repeated = recover_step(workspace, "step-01", "finalize")
            self.assertEqual(repeated.task_status, TaskStatus.WAITING_APPROVAL)

    def test_artifact_metadata_change_changes_approval_fingerprint(self) -> None:
        with TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "input.txt"
            source.write_text("approved", encoding="utf-8")
            workspace, _ = self._workspace(root / "tasks", source)
            workspace.write_text_artifact(
                "source.md",
                "durable",
                kind="report",
                description="source",
                metadata={"classification": "approved"},
            )
            _, _, approved_fingerprint = preview_approval(workspace, "step-01")

            records = workspace._read_json("artifacts.json")
            records[0]["metadata"] = {"classification": "changed"}
            workspace._write_json("artifacts.json", records)
            _, _, changed_fingerprint = preview_approval(workspace, "step-01")

            self.assertNotEqual(approved_fingerprint, changed_fingerprint)

    def test_artifact_order_change_changes_approval_fingerprint(self) -> None:
        with TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "input.txt"
            source.write_text("approved", encoding="utf-8")
            workspace, _ = self._workspace(root / "tasks", source)
            workspace.write_text_artifact("first.md", "first", kind="report", description="first")
            workspace.write_text_artifact("second.md", "second", kind="report", description="second")
            _, _, approved_fingerprint = preview_approval(workspace, "step-01")

            records = workspace._read_json("artifacts.json")
            records.reverse()
            workspace._write_json("artifacts.json", records)
            _, _, reordered_fingerprint = preview_approval(workspace, "step-01")

            self.assertNotEqual(approved_fingerprint, reordered_fingerprint)

    def test_artifact_filename_cannot_escape_task_workspace(self) -> None:
        with TemporaryDirectory() as temp:
            root = Path(temp)
            task = TaskSpec.create("test", "file_report", None, [], [])
            workspace = TaskWorkspace.create(root / "tasks", task)
            escaped = workspace.root.parent / "escaped.txt"

            with self.assertRaises(WorkspaceError):
                workspace.write_text_artifact(
                    str(escaped), "escape", kind="report", description="escape"
                )
            self.assertFalse(escaped.exists())

    async def test_committed_attempt_cannot_be_retried(self) -> None:
        with TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "input.txt"
            source.write_text("approved", encoding="utf-8")
            workspace, task = self._workspace(root / "tasks", source)
            self._committed_attempt(workspace, task)
            await TaskRunner(build_default_registry()).resume(workspace)

            with self.assertRaises(ApprovalError):
                recover_step(workspace, "step-01", "retry")

    async def test_interrupted_remote_attempt_cannot_be_retried(self) -> None:
        with TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "input.txt"
            source.write_text("approved", encoding="utf-8")
            workspace, task = self._workspace(root / "tasks", source)
            approved = approve_step(workspace, "step-01")
            attempt_id = workspace.begin_attempt(
                "step-01", approved.approval_fingerprint or "", "remote_read"
            )
            workspace.start_attempt(attempt_id)
            workspace.end_attempt_context()
            plan = workspace.load_plan()
            plan.steps[0].status = StepStatus.RUNNING
            plan.steps[0].attempt_id = attempt_id
            task.status = TaskStatus.RUNNING
            workspace.save_plan(plan)
            workspace.save_task(task)
            await TaskRunner(build_default_registry()).resume(workspace)

            with self.assertRaises(ApprovalError):
                recover_step(workspace, "step-01", "retry")

    async def test_finalize_rejects_missing_or_tampered_committed_artifact(self) -> None:
        for mutation in ("missing", "tampered"):
            with self.subTest(mutation=mutation), TemporaryDirectory() as temp:
                root = Path(temp)
                source = root / "input.txt"
                source.write_text("approved", encoding="utf-8")
                workspace, task = self._workspace(root / "tasks", source)
                _, artifact = self._committed_attempt(workspace, task)
                await TaskRunner(build_default_registry()).resume(workspace)

                artifact_path = workspace._safe_path(artifact.path)
                if mutation == "missing":
                    artifact_path.unlink()
                else:
                    artifact_path.write_text("tampered", encoding="utf-8")

                with self.assertRaises(WorkspaceError):
                    recover_step(workspace, "step-01", "finalize")

    def test_cli_resume_returns_nonzero_when_recovery_is_required(self) -> None:
        with TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "input.txt"
            source.write_text("approved", encoding="utf-8")
            workspace, task = self._workspace(root / "tasks", source)
            approved = approve_step(workspace, "step-01")
            attempt_id = workspace.begin_attempt("step-01", approved.approval_fingerprint or "")
            workspace.end_attempt_context()
            plan = workspace.load_plan()
            plan.steps[0].status = StepStatus.RUNNING
            plan.steps[0].attempt_id = attempt_id
            task.status = TaskStatus.RUNNING
            workspace.save_plan(plan)
            workspace.save_task(task)

            result = main(
                ["resume", task.id, "--workspace-root", str(root / "tasks")]
            )

            self.assertNotEqual(result, 0)
            self.assertEqual(workspace.load_task().status, TaskStatus.RECOVERY_REQUIRED)

    def test_expected_fingerprint_rejects_preview_approval_race(self) -> None:
        with TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "input.txt"
            source.write_text("approved", encoding="utf-8")
            workspace, _ = self._workspace(root / "tasks", source)
            step, _, expected_fingerprint = preview_approval(workspace, "step-01")
            self.assertEqual(step.status, StepStatus.PLANNED)

            plan = workspace.load_plan()
            plan.steps[0].description = "changed during review"
            workspace.save_plan(plan)
            with self.assertRaises(ApprovalError):
                approve_step(
                    workspace,
                    "step-01",
                    expected_fingerprint=expected_fingerprint,
                )
            self.assertEqual(workspace.load_plan().steps[0].status, StepStatus.PLANNED)

    def test_artifacts_are_versioned_hashed_and_integrity_checked(self) -> None:
        with TemporaryDirectory() as temp:
            root = Path(temp)
            task = TaskSpec.create("test", "file_report", None, [], [])
            workspace = TaskWorkspace.create(root / "tasks", task)
            first = workspace.write_text_artifact("report.md", "one", kind="report", description="one")
            second = workspace.write_text_artifact("report.md", "two", kind="report", description="two")

            self.assertNotEqual(first.path, second.path)
            self.assertEqual(len(first.sha256), 64)
            self.assertEqual(first.size_bytes, 3)
            workspace.artifact_path(first).write_text("tampered", encoding="utf-8")
            with self.assertRaises(WorkspaceError):
                workspace.read_artifact_text(first)

    def test_cross_process_task_lock_rejects_second_owner(self) -> None:
        with TemporaryDirectory() as temp:
            root = Path(temp) / "tasks"
            task = TaskSpec.create("test", "file_report", None, [], [])
            workspace = TaskWorkspace.create(root, task)
            context = multiprocessing.get_context("spawn")
            queue = context.Queue()
            with workspace.lock():
                process = context.Process(target=_try_task_lock, args=(str(root), task.id, queue))
                process.start()
                process.join(10)
            self.assertEqual(process.exitcode, 0)
            self.assertEqual(queue.get(timeout=2), "busy")

    def test_cli_approve_yes_records_fingerprint(self) -> None:
        with TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "input.txt"
            source.write_text("approved", encoding="utf-8")
            workspace, task = self._workspace(root / "tasks", source)
            config = root / "missing-agent.yaml"

            result = main(
                [
                    "approve",
                    task.id,
                    "step-01",
                    "--yes",
                    "--workspace-root",
                    str(root / "tasks"),
                    "--config",
                    str(config),
                ]
            )

            self.assertEqual(result, 0)
            self.assertEqual(len(workspace.load_plan().steps[0].approval_fingerprint or ""), 64)


if __name__ == "__main__":
    unittest.main()

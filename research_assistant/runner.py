from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Optional, Tuple

from .models import PlanStep, StepStatus, TaskPlan, TaskStatus
from .integrity import build_approval_fingerprint
from .providers import ModelProvider
from .registry import ToolContext, ToolRegistry
from .workspace import TaskWorkspace


class ApprovalError(RuntimeError):
    pass


@dataclass
class RunResult:
    task_status: TaskStatus
    executed_step_id: Optional[str] = None
    message: str = ""


def _next_unfinished_step(plan: TaskPlan) -> Optional[PlanStep]:
    return next((step for step in plan.steps if step.status != StepStatus.COMPLETED), None)


def approve_step(
    workspace: TaskWorkspace,
    step_id: str,
    execution_environment: Optional[Dict[str, Any]] = None,
    *,
    expected_fingerprint: Optional[str] = None,
) -> PlanStep:
    with workspace.lock():
        task = workspace.load_task()
        plan = workspace.load_plan()
        current = _next_unfinished_step(plan)
        if current is None:
            raise ApprovalError("Task is already complete.")
        if current.id != step_id:
            raise ApprovalError(f"Only the next unfinished step may be approved: {current.id}")
        if current.status != StepStatus.PLANNED:
            raise ApprovalError(f"Step {step_id} cannot be approved from status {current.status.value}.")
        manifest, fingerprint = build_approval_fingerprint(
            workspace, task, plan, current, execution_environment
        )
        if expected_fingerprint is not None and fingerprint != expected_fingerprint:
            raise ApprovalError("Approval content changed while it was being reviewed; inspect it again.")
        current.status = StepStatus.APPROVED
        current.approval_manifest = manifest
        current.approval_fingerprint = fingerprint
        task.status = TaskStatus.WAITING_APPROVAL
        workspace.save_plan(plan)
        workspace.save_task(task)
        workspace.append_approval(
            {
                "action": "approved",
                "step_id": current.id,
                "tool": current.call.tool_name,
                "risk": current.call.risk.value,
                "target": current.call.target,
                "fingerprint": fingerprint,
                "manifest": manifest,
            }
        )
        return current


def preview_approval(
    workspace: TaskWorkspace,
    step_id: str,
    execution_environment: Optional[Dict[str, Any]] = None,
) -> Tuple[PlanStep, Dict[str, Any], str]:
    """Return the exact approval summary without changing task state."""
    with workspace.lock():
        task = workspace.load_task()
        plan = workspace.load_plan()
        current = _next_unfinished_step(plan)
        if current is None:
            raise ApprovalError("Task is already complete.")
        if current.id != step_id:
            raise ApprovalError(f"Only the next unfinished step may be approved: {current.id}")
        if current.status != StepStatus.PLANNED:
            raise ApprovalError(f"Step {step_id} cannot be approved from status {current.status.value}.")
        manifest, fingerprint = build_approval_fingerprint(
            workspace, task, plan, current, execution_environment
        )
        return current, manifest, fingerprint


class TaskRunner:
    def __init__(
        self,
        registry: ToolRegistry,
        provider: Optional[ModelProvider] = None,
        execution_environment: Optional[Dict[str, Any]] = None,
    ):
        self.registry = registry
        self.provider = provider
        self.execution_environment = execution_environment

    async def resume(self, workspace: TaskWorkspace) -> RunResult:
        with workspace.lock():
            task = workspace.load_task()
            plan = workspace.load_plan()
            current = _next_unfinished_step(plan)
            if current is None:
                task.status = TaskStatus.COMPLETED
                workspace.save_task(task)
                return RunResult(TaskStatus.COMPLETED, message="Task is already complete.")
            if current.status == StepStatus.PLANNED:
                task.status = TaskStatus.WAITING_APPROVAL
                workspace.save_task(task)
                return RunResult(task.status, message=f"Waiting for approval of {current.id}.")
            if current.status == StepStatus.REJECTED:
                task.status = TaskStatus.WAITING_APPROVAL
                workspace.save_task(task)
                return RunResult(task.status, message=f"Step {current.id} was rejected.")
            if current.status == StepStatus.FAILED:
                task.status = TaskStatus.FAILED
                workspace.save_task(task)
                return RunResult(task.status, message=f"Step {current.id} previously failed: {current.error or ''}")
            if current.status == StepStatus.RUNNING:
                current.status = StepStatus.RECOVERY_REQUIRED
                task.status = TaskStatus.RECOVERY_REQUIRED
                workspace.save_plan(plan)
                workspace.save_task(task)
                workspace.append_log(
                    "step_recovery_required",
                    {"step_id": current.id, "attempt_id": current.attempt_id},
                )
                return RunResult(
                    task.status,
                    message=f"Step {current.id} needs explicit recovery; it was interrupted while running.",
                )
            if current.status == StepStatus.RECOVERY_REQUIRED:
                task.status = TaskStatus.RECOVERY_REQUIRED
                workspace.save_task(task)
                return RunResult(task.status, message=f"Step {current.id} needs explicit recovery.")
            if current.status != StepStatus.APPROVED:
                raise ApprovalError(f"Step {current.id} has unexpected status {current.status.value}.")

            manifest, fingerprint = build_approval_fingerprint(
                workspace, task, plan, current, self.execution_environment
            )
            if not current.approval_fingerprint or fingerprint != current.approval_fingerprint:
                previous = current.approval_fingerprint
                current.status = StepStatus.PLANNED
                current.approval_fingerprint = None
                current.approval_manifest = {}
                task.status = TaskStatus.WAITING_APPROVAL
                workspace.save_plan(plan)
                workspace.save_task(task)
                workspace.append_approval(
                    {
                        "action": "approval_invalidated",
                        "step_id": current.id,
                        "approved_fingerprint": previous,
                        "current_fingerprint": fingerprint,
                        "current_manifest": manifest,
                    }
                )
                raise ApprovalError("Approved execution content changed; review and approve the step again.")

            tool = self.registry.validate_call(current.call.tool_name, current.call.arguments)
            recovery_strategy = str(getattr(tool, "recovery_strategy", "unknown"))
            attempt_id = workspace.begin_attempt(current.id, fingerprint, recovery_strategy)
            current.attempt_id = attempt_id
            current.status = StepStatus.RUNNING
            task.status = TaskStatus.RUNNING
            workspace.save_plan(plan)
            workspace.save_task(task)
            attempt = workspace.start_attempt(attempt_id)
            workspace.append_log(
                "step_started",
                {
                    "step_id": current.id,
                    "attempt_id": attempt_id,
                    "tool": current.call.tool_name,
                    "target": current.call.target,
                    "approval_fingerprint": fingerprint,
                },
            )
            try:
                result = await tool.run(
                    ToolContext(
                        task=task,
                        workspace=workspace,
                        provider=self.provider,
                        attempt_id=attempt_id,
                        idempotency_key=str(attempt["idempotency_key"]),
                    ),
                    current.call.arguments,
                )
            except Exception as exc:
                workspace.finish_attempt(attempt_id, "failed", str(exc))
                current.status = StepStatus.FAILED
                current.error = str(exc)
                task.status = TaskStatus.FAILED
                workspace.save_plan(plan)
                workspace.save_task(task)
                workspace.append_log(
                    "step_failed",
                    {"step_id": current.id, "attempt_id": attempt_id, "error": str(exc)},
                )
                return RunResult(task.status, executed_step_id=current.id, message=f"Step failed: {exc}")
            finally:
                workspace.end_attempt_context()

            committed = workspace.commit_attempt(attempt_id, result.summary)
            workspace.mark_attempt_finalized(attempt_id)
            current.status = StepStatus.COMPLETED
            current.artifact_ids = [artifact.id for artifact in committed]
            current.error = None
            next_step = _next_unfinished_step(plan)
            task.status = TaskStatus.COMPLETED if next_step is None else TaskStatus.WAITING_APPROVAL
            workspace.save_plan(plan)
            workspace.save_task(task)
            workspace.append_log(
                "step_completed",
                {
                    "step_id": current.id,
                    "attempt_id": attempt_id,
                    "summary": result.summary,
                    "artifact_ids": current.artifact_ids,
                    "next_step_id": next_step.id if next_step else None,
                },
            )
            return RunResult(task.status, executed_step_id=current.id, message=result.summary)


def recover_step(workspace: TaskWorkspace, step_id: str, action: str) -> RunResult:
    if action not in {"finalize", "retry", "fail"}:
        raise ApprovalError(f"Unsupported recovery action: {action}")
    with workspace.lock():
        task = workspace.load_task()
        plan = workspace.load_plan()
        current = next((step for step in plan.steps if step.id == step_id), None)
        if current is None:
            raise ApprovalError(f"Unknown step: {step_id}")
        next_unfinished = _next_unfinished_step(plan)
        if current.status != StepStatus.COMPLETED and current is not next_unfinished:
            raise ApprovalError("Only the next unfinished step may be recovered.")
        if current.status == StepStatus.COMPLETED:
            if action != "finalize" or not current.attempt_id:
                raise ApprovalError(f"Step {step_id} is already complete.")
            attempt = workspace.attempt(current.attempt_id)
            if attempt.get("status") not in {"committed", "finalized"}:
                raise ApprovalError("Completed step has no durable committed attempt marker.")
            if attempt.get("step_id") != current.id:
                raise ApprovalError("Attempt record does not belong to this step.")
            if attempt.get("approval_fingerprint") != current.approval_fingerprint:
                raise ApprovalError("Attempt record does not match the approved execution fingerprint.")
            committed = workspace.finalize_attempt_artifacts(current.attempt_id)
            artifact_ids = [artifact.id for artifact in committed]
            if current.artifact_ids != artifact_ids:
                raise ApprovalError("Completed step artifact list does not match its committed attempt.")
            workspace.mark_attempt_finalized(current.attempt_id)
            task.status = TaskStatus.COMPLETED if _next_unfinished_step(plan) is None else TaskStatus.WAITING_APPROVAL
            workspace.save_task(task)
            return RunResult(task.status, executed_step_id=step_id, message="Attempt was already finalized.")
        if current.status not in {StepStatus.RUNNING, StepStatus.RECOVERY_REQUIRED}:
            raise ApprovalError(f"Step {step_id} does not require recovery.")
        if not current.attempt_id:
            raise ApprovalError(f"Step {step_id} has no recoverable attempt record.")
        attempt = workspace.attempt(current.attempt_id)
        if attempt.get("step_id") != current.id:
            raise ApprovalError("Attempt record does not belong to this step.")
        if attempt.get("approval_fingerprint") != current.approval_fingerprint:
            raise ApprovalError("Attempt record does not match the approved execution fingerprint.")
        attempt_status = str(attempt.get("status", ""))

        if action == "finalize":
            if attempt_status not in {"committed", "finalized"}:
                raise ApprovalError("Only an attempt with a durable committed marker may be finalized.")
            committed = workspace.finalize_attempt_artifacts(current.attempt_id)
            workspace.mark_attempt_finalized(current.attempt_id)
            current.status = StepStatus.COMPLETED
            current.artifact_ids = [artifact.id for artifact in committed]
            current.error = None
            next_step = _next_unfinished_step(plan)
            task.status = TaskStatus.COMPLETED if next_step is None else TaskStatus.WAITING_APPROVAL
        elif action == "retry":
            if attempt_status in {"committed", "finalized"}:
                raise ApprovalError("A committed attempt can only be finalized; retry would duplicate its result.")
            if attempt_status == "running" and attempt.get("recovery_strategy") != "local_deterministic":
                raise ApprovalError("Interrupted remote, model, or unknown tools cannot be retried safely.")
            if attempt_status not in {"prepared", "running"}:
                raise ApprovalError(f"Attempt cannot be retried from status {attempt_status!r}.")
            workspace.finish_attempt(current.attempt_id, "abandoned", "Explicitly reset for retry.")
            current.status = StepStatus.PLANNED
            current.error = None
            current.approval_fingerprint = None
            current.approval_manifest = {}
            current.attempt_id = None
            task.status = TaskStatus.WAITING_APPROVAL
        else:
            if attempt_status in {"committed", "finalized"}:
                raise ApprovalError("A committed attempt can only be finalized.")
            workspace.finish_attempt(current.attempt_id, "failed", "Marked failed during recovery.")
            current.status = StepStatus.FAILED
            current.error = "Marked failed during recovery."
            task.status = TaskStatus.FAILED

        workspace.save_plan(plan)
        workspace.save_task(task)
        workspace.append_log(
            "step_recovered",
            {"step_id": step_id, "attempt_id": attempt.get("attempt_id"), "action": action},
        )
        return RunResult(task.status, executed_step_id=step_id, message=f"Recovery action applied: {action}.")

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from .models import PlanStep, StepStatus, TaskPlan, TaskStatus
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


def approve_step(workspace: TaskWorkspace, step_id: str) -> PlanStep:
    task = workspace.load_task()
    plan = workspace.load_plan()
    current = _next_unfinished_step(plan)
    if current is None:
        raise ApprovalError("Task is already complete.")
    if current.id != step_id:
        raise ApprovalError(f"Only the next unfinished step may be approved: {current.id}")
    if current.status != StepStatus.PLANNED:
        raise ApprovalError(f"Step {step_id} cannot be approved from status {current.status.value}.")
    current.status = StepStatus.APPROVED
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
        }
    )
    return current


class TaskRunner:
    def __init__(self, registry: ToolRegistry, provider: Optional[ModelProvider] = None):
        self.registry = registry
        self.provider = provider

    async def resume(self, workspace: TaskWorkspace) -> RunResult:
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
        if current.status != StepStatus.APPROVED:
            raise ApprovalError(f"Step {current.id} has unexpected status {current.status.value}.")

        tool = self.registry.validate_call(current.call.tool_name, current.call.arguments)
        current.status = StepStatus.RUNNING
        task.status = TaskStatus.RUNNING
        workspace.save_plan(plan)
        workspace.save_task(task)
        workspace.append_log(
            "step_started",
            {"step_id": current.id, "tool": current.call.tool_name, "target": current.call.target},
        )
        try:
            result = await tool.run(ToolContext(task=task, workspace=workspace, provider=self.provider), current.call.arguments)
        except Exception as exc:
            current.status = StepStatus.FAILED
            current.error = str(exc)
            task.status = TaskStatus.FAILED
            workspace.save_plan(plan)
            workspace.save_task(task)
            workspace.append_log("step_failed", {"step_id": current.id, "error": str(exc)})
            return RunResult(task.status, executed_step_id=current.id, message=f"Step failed: {exc}")

        current.status = StepStatus.COMPLETED
        current.artifact_ids = [artifact.id for artifact in result.artifacts]
        current.error = None
        next_step = _next_unfinished_step(plan)
        task.status = TaskStatus.COMPLETED if next_step is None else TaskStatus.WAITING_APPROVAL
        workspace.save_plan(plan)
        workspace.save_task(task)
        workspace.append_log(
            "step_completed",
            {
                "step_id": current.id,
                "summary": result.summary,
                "artifact_ids": current.artifact_ids,
                "next_step_id": next_step.id if next_step else None,
            },
        )
        return RunResult(task.status, executed_step_id=current.id, message=result.summary)

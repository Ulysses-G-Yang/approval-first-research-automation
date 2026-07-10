from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional
from uuid import uuid4


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class RiskLevel(str, Enum):
    READ = "read"
    WRITE = "write"
    SENSITIVE = "sensitive"


class StepStatus(str, Enum):
    PLANNED = "planned"
    APPROVED = "approved"
    RUNNING = "running"
    COMPLETED = "completed"
    REJECTED = "rejected"
    FAILED = "failed"


class TaskStatus(str, Enum):
    PLANNED = "planned"
    WAITING_APPROVAL = "waiting_approval"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


@dataclass
class Artifact:
    id: str
    kind: str
    path: str
    description: str
    created_at: str = field(default_factory=utc_now)
    source_url: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "kind": self.kind,
            "path": self.path,
            "description": self.description,
            "created_at": self.created_at,
            "source_url": self.source_url,
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Artifact":
        return cls(
            id=str(data["id"]),
            kind=str(data["kind"]),
            path=str(data["path"]),
            description=str(data.get("description", "")),
            created_at=str(data.get("created_at", utc_now())),
            source_url=data.get("source_url"),
            metadata=dict(data.get("metadata") or {}),
        )


@dataclass
class Approval:
    task_id: str
    action: str
    step_id: Optional[str] = None
    created_at: str = field(default_factory=utc_now)
    details: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "task_id": self.task_id,
            "action": self.action,
            "step_id": self.step_id,
            "created_at": self.created_at,
            "details": self.details,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Approval":
        return cls(
            task_id=str(data["task_id"]),
            action=str(data["action"]),
            step_id=data.get("step_id"),
            created_at=str(data.get("created_at", utc_now())),
            details=dict(data.get("details") or {}),
        )


@dataclass
class ToolCall:
    tool_name: str
    arguments: Dict[str, Any]
    risk: RiskLevel
    target: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "tool_name": self.tool_name,
            "arguments": self.arguments,
            "risk": self.risk.value,
            "target": self.target,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ToolCall":
        return cls(
            tool_name=str(data["tool_name"]),
            arguments=dict(data.get("arguments") or {}),
            risk=RiskLevel(str(data.get("risk", RiskLevel.READ.value))),
            target=str(data.get("target", "")),
        )


@dataclass
class PlanStep:
    id: str
    description: str
    call: ToolCall
    status: StepStatus = StepStatus.PLANNED
    artifact_ids: List[str] = field(default_factory=list)
    error: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "description": self.description,
            "call": self.call.to_dict(),
            "status": self.status.value,
            "artifact_ids": self.artifact_ids,
            "error": self.error,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "PlanStep":
        return cls(
            id=str(data["id"]),
            description=str(data.get("description", "")),
            call=ToolCall.from_dict(dict(data["call"])),
            status=StepStatus(str(data.get("status", StepStatus.PLANNED.value))),
            artifact_ids=[str(item) for item in data.get("artifact_ids") or []],
            error=data.get("error"),
        )


@dataclass
class TaskSpec:
    id: str
    goal: str
    workflow: str
    provider_name: Optional[str]
    urls: List[str] = field(default_factory=list)
    input_files: List[str] = field(default_factory=list)
    options: Dict[str, Any] = field(default_factory=dict)
    created_at: str = field(default_factory=utc_now)
    status: TaskStatus = TaskStatus.PLANNED
    summary: str = ""

    @classmethod
    def create(
        cls,
        goal: str,
        workflow: str,
        provider_name: Optional[str],
        urls: List[str],
        input_files: List[str],
        options: Optional[Dict[str, Any]] = None,
    ) -> "TaskSpec":
        return cls(
            id=uuid4().hex[:12],
            goal=goal,
            workflow=workflow,
            provider_name=provider_name,
            urls=urls,
            input_files=input_files,
            options=dict(options or {}),
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "goal": self.goal,
            "workflow": self.workflow,
            "provider_name": self.provider_name,
            "urls": self.urls,
            "input_files": self.input_files,
            "options": self.options,
            "created_at": self.created_at,
            "status": self.status.value,
            "summary": self.summary,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "TaskSpec":
        return cls(
            id=str(data["id"]),
            goal=str(data["goal"]),
            workflow=str(data["workflow"]),
            provider_name=data.get("provider_name"),
            urls=[str(item) for item in data.get("urls") or []],
            input_files=[str(item) for item in data.get("input_files") or []],
            options=dict(data.get("options") or {}),
            created_at=str(data.get("created_at", utc_now())),
            status=TaskStatus(str(data.get("status", TaskStatus.PLANNED.value))),
            summary=str(data.get("summary", "")),
        )


@dataclass
class TaskPlan:
    task_id: str
    summary: str
    steps: List[PlanStep]
    created_at: str = field(default_factory=utc_now)
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "task_id": self.task_id,
            "summary": self.summary,
            "steps": [step.to_dict() for step in self.steps],
            "created_at": self.created_at,
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "TaskPlan":
        return cls(
            task_id=str(data["task_id"]),
            summary=str(data.get("summary", "")),
            steps=[PlanStep.from_dict(dict(step)) for step in data.get("steps") or []],
            created_at=str(data.get("created_at", utc_now())),
            metadata=dict(data.get("metadata") or {}),
        )

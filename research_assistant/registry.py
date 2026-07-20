from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Protocol

from .models import Artifact, RiskLevel, TaskSpec
from .providers import ModelProvider
from .workspace import TaskWorkspace, WorkspaceError


class ToolError(RuntimeError):
    pass


class ToolPermissionError(ToolError):
    pass


@dataclass
class ToolResult:
    summary: str
    artifacts: List[Artifact] = field(default_factory=list)
    details: Dict[str, Any] = field(default_factory=dict)


@dataclass
class ToolContext:
    task: TaskSpec
    workspace: TaskWorkspace
    provider: Optional[ModelProvider] = None
    attempt_id: Optional[str] = None
    idempotency_key: Optional[str] = None

    def resolve_input(self, raw_path: str) -> Path:
        try:
            return self.workspace.resolve_input_snapshot(raw_path, self.task)
        except WorkspaceError as exc:
            raise ToolPermissionError(str(exc)) from exc

    def artifacts(self, kind: Optional[str] = None) -> List[Artifact]:
        return self.workspace.list_artifacts(kind=kind)


class Tool(Protocol):
    name: str
    description: str
    risk: RiskLevel
    required_arguments: tuple[str, ...]
    allowed_arguments: tuple[str, ...]
    recovery_strategy: str

    async def run(self, context: ToolContext, arguments: Dict[str, Any]) -> ToolResult:
        ...


class ToolRegistry:
    def __init__(self):
        self._tools: Dict[str, Tool] = {}

    def register(self, tool: Tool) -> None:
        if tool.name in self._tools:
            raise ToolError(f"Tool already registered: {tool.name}")
        self._tools[tool.name] = tool

    def get(self, name: str) -> Tool:
        try:
            return self._tools[name]
        except KeyError as exc:
            raise ToolError(f"Unknown tool: {name}") from exc

    def names(self) -> List[str]:
        return sorted(self._tools)

    def describe(self) -> List[Dict[str, Any]]:
        return [
            {
                "name": tool.name,
                "description": tool.description,
                "risk": tool.risk.value,
                "required_arguments": list(tool.required_arguments),
                "allowed_arguments": list(getattr(tool, "allowed_arguments", ())),
                "recovery_strategy": getattr(tool, "recovery_strategy", "unknown"),
            }
            for tool in (self._tools[name] for name in self.names())
        ]

    def validate_call(self, name: str, arguments: Dict[str, Any]) -> Tool:
        if not isinstance(arguments, dict):
            raise ToolError(f"Tool {name} arguments must be an object.")
        tool = self.get(name)
        missing = [key for key in tool.required_arguments if key not in arguments]
        if missing:
            raise ToolError(f"Tool {name} is missing required arguments: {', '.join(missing)}")
        allowed = getattr(tool, "allowed_arguments", None)
        if allowed is not None:
            unexpected = sorted(set(arguments) - set(allowed))
            if unexpected:
                raise ToolError(f"Tool {name} received unsupported arguments: {', '.join(unexpected)}")
        return tool

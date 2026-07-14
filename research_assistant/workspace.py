from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional
from uuid import uuid4

from .models import Artifact, TaskPlan, TaskSpec, utc_now


class WorkspaceError(RuntimeError):
    pass


def default_workspace_root() -> Path:
    return Path.home() / "GenericCrawler" / "tasks"


class TaskWorkspace:
    def __init__(self, root: Path):
        self.root = root.resolve()
        self.artifacts_dir = self.root / "artifacts"

    @classmethod
    def create(cls, root: Path, task: TaskSpec) -> "TaskWorkspace":
        workspace = cls(root.expanduser().resolve() / task.id)
        workspace.root.mkdir(parents=True, exist_ok=False)
        workspace.artifacts_dir.mkdir()
        workspace.save_task(task)
        workspace._write_json("artifacts.json", [])
        workspace.append_log("task_created", {"goal": task.goal, "workflow": task.workflow})
        return workspace

    @classmethod
    def open(cls, root: Path, task_id: str) -> "TaskWorkspace":
        base = root.expanduser().resolve()
        candidate = (base / task_id).resolve()
        if candidate == base or base not in candidate.parents:
            raise WorkspaceError("Task id must resolve inside the configured task workspace root.")
        workspace = cls(candidate)
        if not workspace.root.is_dir():
            raise WorkspaceError(f"Task workspace does not exist: {workspace.root}")
        return workspace

    def _safe_path(self, relative_path: str | Path) -> Path:
        candidate = (self.root / relative_path).resolve()
        if candidate != self.root and self.root not in candidate.parents:
            raise WorkspaceError("Artifact path must stay inside the task workspace.")
        return candidate

    def _write_json(self, relative_path: str | Path, value: Any) -> Path:
        target = self._safe_path(relative_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        temp = target.with_suffix(target.suffix + ".tmp")
        temp.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
        temp.replace(target)
        return target

    def _read_json(self, relative_path: str | Path, default: Any = None) -> Any:
        target = self._safe_path(relative_path)
        if not target.exists():
            return default
        return json.loads(target.read_text(encoding="utf-8"))

    def save_task(self, task: TaskSpec) -> None:
        self._write_json("task.json", task.to_dict())

    def load_task(self) -> TaskSpec:
        data = self._read_json("task.json")
        if not isinstance(data, dict):
            raise WorkspaceError("task.json is missing or invalid.")
        return TaskSpec.from_dict(data)

    def save_plan(self, plan: TaskPlan) -> None:
        self._write_json("plan.json", plan.to_dict())

    def load_plan(self) -> TaskPlan:
        data = self._read_json("plan.json")
        if not isinstance(data, dict):
            raise WorkspaceError("plan.json is missing or invalid.")
        return TaskPlan.from_dict(data)

    def append_approval(self, event: Dict[str, Any]) -> None:
        event = {"at": utc_now(), **event}
        target = self._safe_path("approvals.jsonl")
        with target.open("a", encoding="utf-8", newline="\n") as handle:
            handle.write(json.dumps(event, ensure_ascii=False) + "\n")

    def append_log(self, event: str, details: Optional[Dict[str, Any]] = None) -> None:
        entry = {"at": utc_now(), "event": event, "details": details or {}}
        target = self._safe_path("run.jsonl")
        with target.open("a", encoding="utf-8", newline="\n") as handle:
            handle.write(json.dumps(entry, ensure_ascii=False) + "\n")

    def list_artifacts(self, kind: Optional[str] = None) -> List[Artifact]:
        raw = self._read_json("artifacts.json", default=[])
        artifacts = [Artifact.from_dict(dict(item)) for item in raw if isinstance(item, dict)]
        return [artifact for artifact in artifacts if kind is None or artifact.kind == kind]

    def artifact_by_id(self, artifact_id: str) -> Artifact:
        for artifact in self.list_artifacts():
            if artifact.id == artifact_id:
                return artifact
        raise WorkspaceError(f"Artifact not found: {artifact_id}")

    def read_artifact_text(self, artifact: Artifact) -> str:
        return self._safe_path(artifact.path).read_text(encoding="utf-8")

    def read_artifact_bytes(self, artifact: Artifact) -> bytes:
        return self._safe_path(artifact.path).read_bytes()

    def artifact_path(self, artifact: Artifact) -> Path:
        return self._safe_path(artifact.path)

    def read_artifact_json(self, artifact: Artifact) -> Any:
        return json.loads(self.read_artifact_text(artifact))

    def write_text_artifact(
        self,
        filename: str,
        content: str,
        *,
        kind: str,
        description: str,
        source_url: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Artifact:
        target = self._safe_path(Path("artifacts") / filename)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        return self._register_artifact(
            target,
            kind=kind,
            description=description,
            source_url=source_url,
            metadata=metadata,
        )

    def write_json_artifact(
        self,
        filename: str,
        value: Any,
        *,
        kind: str,
        description: str,
        source_url: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Artifact:
        target = self._safe_path(Path("artifacts") / filename)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
        return self._register_artifact(
            target,
            kind=kind,
            description=description,
            source_url=source_url,
            metadata=metadata,
        )

    def write_bytes_artifact(
        self,
        filename: str,
        content: bytes,
        *,
        kind: str,
        description: str,
        source_url: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Artifact:
        target = self._safe_path(Path("artifacts") / filename)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)
        return self._register_artifact(
            target,
            kind=kind,
            description=description,
            source_url=source_url,
            metadata=metadata,
        )

    def _register_artifact(
        self,
        target: Path,
        *,
        kind: str,
        description: str,
        source_url: Optional[str],
        metadata: Optional[Dict[str, Any]],
    ) -> Artifact:
        artifact = Artifact(
            id=uuid4().hex[:12],
            kind=kind,
            path=str(target.relative_to(self.root)).replace("\\", "/"),
            description=description,
            source_url=source_url,
            metadata=metadata or {},
        )
        artifacts = [item.to_dict() for item in self.list_artifacts()]
        artifacts.append(artifact.to_dict())
        self._write_json("artifacts.json", artifacts)
        self.append_log("artifact_written", {"artifact_id": artifact.id, "kind": kind, "path": artifact.path})
        return artifact

    def export_archive(self, destination: Path) -> Path:
        import shutil

        destination = destination.expanduser().resolve()
        destination.parent.mkdir(parents=True, exist_ok=True)
        base_name = str(destination.with_suffix(""))
        archive = shutil.make_archive(base_name, "zip", root_dir=self.root)
        return Path(archive)

from __future__ import annotations

import hashlib
import json
import os
import re
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Dict, Iterable, Iterator, List, Optional
from uuid import uuid4

from .models import Artifact, TaskPlan, TaskSpec, utc_now


_MARKDOWN_IMAGE_PATTERN = re.compile(r"!\[[^\]]*\]\(([^)\s]+)(?:\s+\"[^\"]*\")?\)")
_MAX_SNAPSHOTTED_MARKDOWN_ASSETS = 100


class WorkspaceError(RuntimeError):
    pass


class TaskBusyError(WorkspaceError):
    pass


def default_workspace_root() -> Path:
    return Path.home() / "GenericCrawler" / "tasks"


class TaskWorkspace:
    def __init__(self, root: Path):
        self.root = root.resolve()
        self.artifacts_dir = self.root / "artifacts"
        self._active_step_id: Optional[str] = None
        self._active_attempt_id: Optional[str] = None

    @classmethod
    def create(cls, root: Path, task: TaskSpec) -> "TaskWorkspace":
        workspace = cls(root.expanduser().resolve() / task.id)
        workspace.root.mkdir(parents=True, exist_ok=False)
        workspace.artifacts_dir.mkdir()
        workspace.snapshot_task_inputs(task)
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

    @contextmanager
    def lock(self, timeout_seconds: float = 0.0) -> Iterator[None]:
        """Hold one cross-process lock for a task mutation or execution."""
        target = self._safe_path(".task.lock")
        target.parent.mkdir(parents=True, exist_ok=True)
        handle = target.open("a+b")
        handle.seek(0, os.SEEK_END)
        if handle.tell() == 0:
            handle.write(b"0")
            handle.flush()
        deadline = time.monotonic() + max(timeout_seconds, 0.0)
        acquired = False
        try:
            while True:
                try:
                    handle.seek(0)
                    if os.name == "nt":
                        import msvcrt

                        msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                    else:  # pragma: no cover - exercised in Linux CI
                        import fcntl

                        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                    acquired = True
                    break
                except OSError as exc:
                    if time.monotonic() >= deadline:
                        raise TaskBusyError("Another process is already operating on this task.") from exc
                    time.sleep(0.05)
            yield
        finally:
            if acquired:
                handle.seek(0)
                if os.name == "nt":
                    import msvcrt

                    msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
                else:  # pragma: no cover - exercised in Linux CI
                    import fcntl

                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
            handle.close()

    def _write_json(self, relative_path: str | Path, value: Any) -> Path:
        target = self._safe_path(relative_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        self._atomic_write_bytes(target, json.dumps(value, ensure_ascii=False, indent=2).encode("utf-8"))
        return target

    def _atomic_write_bytes(self, target: Path, content: bytes) -> None:
        temp = target.with_name(f".{target.name}.{uuid4().hex}.tmp")
        try:
            with temp.open("wb") as handle:
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
            temp.replace(target)
            if os.name != "nt":  # pragma: no cover - exercised in Linux CI
                directory = os.open(target.parent, os.O_RDONLY)
                try:
                    os.fsync(directory)
                finally:
                    os.close(directory)
        finally:
            if temp.exists():
                temp.unlink()

    def _read_json(self, relative_path: str | Path, default: Any = None) -> Any:
        target = self._safe_path(relative_path)
        if not target.exists():
            return default
        return json.loads(target.read_text(encoding="utf-8"))

    def snapshot_task_inputs(self, task: TaskSpec) -> List[Dict[str, Any]]:
        """Copy user inputs once, before planning, and make snapshots the execution inputs."""
        records: List[Dict[str, Any]] = []
        snapshot_paths: List[str] = []
        inputs_root = self._safe_path("inputs")
        for raw_path in list(task.input_files):
            source = Path(raw_path).expanduser().resolve()
            if not source.is_file():
                raise WorkspaceError(f"Input file does not exist: {source}")
            content = source.read_bytes()
            digest = hashlib.sha256(content).hexdigest()
            asset_sources = self._markdown_asset_sources(source, content)
            bundle_hash = hashlib.sha256()
            bundle_hash.update(content)
            for reference, _asset_source, asset_content in asset_sources:
                bundle_hash.update(b"\0asset\0")
                bundle_hash.update(reference.encode("utf-8"))
                bundle_hash.update(b"\0")
                bundle_hash.update(hashlib.sha256(asset_content).digest())
            snapshot_key = bundle_hash.hexdigest()
            filename = source.name
            if not filename:
                raise WorkspaceError(f"Input file has no usable name: {source}")
            target = (inputs_root / snapshot_key / filename).resolve()
            if target == inputs_root or inputs_root not in target.parents:
                raise WorkspaceError("Input snapshot path must stay inside the task workspace.")
            target.parent.mkdir(parents=True, exist_ok=True)
            if target.exists():
                if target.read_bytes() != content:
                    raise WorkspaceError(f"Input snapshot hash collision: {source}")
            else:
                self._atomic_write_bytes(target, content)
            asset_records = []
            for reference, asset_source, asset_content in asset_sources:
                asset_target = (target.parent / reference).resolve()
                if asset_target == target.parent or target.parent not in asset_target.parents:
                    raise WorkspaceError(f"Markdown asset escapes its input directory: {reference}")
                asset_target.parent.mkdir(parents=True, exist_ok=True)
                if asset_target.exists():
                    if asset_target.read_bytes() != asset_content:
                        raise WorkspaceError(f"Input snapshot asset collision: {asset_source}")
                else:
                    self._atomic_write_bytes(asset_target, asset_content)
                asset_records.append(
                    {
                        "reference": reference,
                        "original_path": str(asset_source),
                        "snapshot_path": str(asset_target.relative_to(self.root)).replace("\\", "/"),
                        "sha256": hashlib.sha256(asset_content).hexdigest(),
                        "size_bytes": len(asset_content),
                    }
                )
            relative = str(target.relative_to(self.root)).replace("\\", "/")
            records.append(
                {
                    "original_path": str(source),
                    "snapshot_path": relative,
                    "sha256": digest,
                    "size_bytes": len(content),
                    "assets": asset_records,
                }
            )
            snapshot_paths.append(str(target))
        task.input_files = snapshot_paths
        self._write_json("inputs.json", records)
        return records

    @staticmethod
    def _markdown_asset_sources(source: Path, content: bytes) -> List[tuple[str, Path, bytes]]:
        if source.suffix.lower() not in {".md", ".markdown"}:
            return []
        try:
            text = content.decode("utf-8-sig")
        except UnicodeDecodeError:
            return []
        base = source.parent.resolve()
        found: List[tuple[str, Path, bytes]] = []
        seen = set()
        for match in _MARKDOWN_IMAGE_PATTERN.finditer(text):
            reference = match.group(1)
            if reference in seen or reference.startswith(("http://", "https://", "data:")):
                continue
            candidate = (base / reference).resolve()
            if candidate == base or base not in candidate.parents or not candidate.is_file():
                continue
            seen.add(reference)
            found.append((reference, candidate, candidate.read_bytes()))
            if len(found) > _MAX_SNAPSHOTTED_MARKDOWN_ASSETS:
                raise WorkspaceError(
                    f"Markdown input exceeded {_MAX_SNAPSHOTTED_MARKDOWN_ASSETS} local image assets."
                )
        return found

    def verify_input_snapshots(self, task: TaskSpec) -> List[Dict[str, Any]]:
        raw = self._read_json("inputs.json")
        if not isinstance(raw, list):
            if task.input_files:
                raise WorkspaceError("This task predates input snapshots; create a new task before approval.")
            raise WorkspaceError("inputs.json is missing or invalid.")
        records = [dict(item) for item in raw if isinstance(item, dict)]
        if len(records) != len(raw) or len(records) != len(task.input_files):
            raise WorkspaceError("Input snapshot manifest does not match the task inputs.")
        verified: List[Dict[str, Any]] = []
        inputs_root = self._safe_path("inputs")
        registered_paths = set()
        for expected_path, record in zip(task.input_files, records):
            relative = str(record.get("snapshot_path", ""))
            snapshot = self._safe_path(relative)
            if snapshot == inputs_root or inputs_root not in snapshot.parents:
                raise WorkspaceError("Input snapshot must stay inside the workspace inputs directory.")
            if snapshot != Path(expected_path).expanduser().resolve():
                raise WorkspaceError("Task input path does not match its immutable snapshot manifest.")
            if not snapshot.is_file():
                raise WorkspaceError(f"Input snapshot is missing: {relative}")
            registered_paths.add(snapshot)
            content = snapshot.read_bytes()
            digest = hashlib.sha256(content).hexdigest()
            if digest != str(record.get("sha256", "")) or len(content) != int(record.get("size_bytes", -1)):
                raise WorkspaceError(f"Input snapshot integrity check failed: {relative}")
            verified_assets = []
            raw_assets = record.get("assets") or []
            if not isinstance(raw_assets, list):
                raise WorkspaceError(f"Input snapshot asset manifest is invalid: {relative}")
            for raw_asset in raw_assets:
                if not isinstance(raw_asset, dict):
                    raise WorkspaceError(f"Input snapshot asset manifest is invalid: {relative}")
                asset = dict(raw_asset)
                asset_path = self._safe_path(str(asset.get("snapshot_path", "")))
                if asset_path == snapshot.parent or snapshot.parent not in asset_path.parents:
                    raise WorkspaceError("Input snapshot asset must stay beside its snapshotted document.")
                if not asset_path.is_file():
                    raise WorkspaceError(f"Input snapshot asset is missing: {asset.get('snapshot_path')}")
                registered_paths.add(asset_path)
                asset_content = asset_path.read_bytes()
                asset_digest = hashlib.sha256(asset_content).hexdigest()
                if asset_digest != str(asset.get("sha256", "")) or len(asset_content) != int(
                    asset.get("size_bytes", -1)
                ):
                    raise WorkspaceError(f"Input snapshot asset integrity check failed: {asset.get('snapshot_path')}")
                verified_assets.append(
                    {
                        "reference": str(asset.get("reference", "")),
                        "original_path": str(asset.get("original_path", "")),
                        "snapshot_path": str(asset.get("snapshot_path", "")),
                        "sha256": asset_digest,
                        "size_bytes": len(asset_content),
                    }
                )
            verified.append(
                {
                    "original_path": str(record.get("original_path", "")),
                    "snapshot_path": relative,
                    "sha256": digest,
                    "size_bytes": len(content),
                    "assets": verified_assets,
                }
            )
        actual_paths = {path.resolve() for path in inputs_root.rglob("*") if path.is_file()} if inputs_root.exists() else set()
        if actual_paths != registered_paths:
            raise WorkspaceError("Workspace inputs contain files that are not in the immutable snapshot manifest.")
        return verified

    def resolve_input_snapshot(self, raw_path: str, task: TaskSpec) -> Path:
        records = self.verify_input_snapshots(task)
        candidate = Path(raw_path).expanduser().resolve()
        allowed = {Path(path).expanduser().resolve() for path in task.input_files}
        for record in records:
            snapshot = self._safe_path(str(record["snapshot_path"]))
            original_value = str(record.get("original_path", ""))
            original = Path(original_value).expanduser().resolve() if original_value else None
            if candidate == snapshot or (original is not None and candidate == original):
                if snapshot not in allowed:
                    break
                return snapshot
        raise WorkspaceError("Tool may only read immutable snapshots of files supplied with --input.")

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

    def list_artifacts(self, kind: Optional[str] = None, *, include_uncommitted: bool = False) -> List[Artifact]:
        raw = self._read_json("artifacts.json", default=[])
        artifacts = [Artifact.from_dict(dict(item)) for item in raw if isinstance(item, dict)]
        if not include_uncommitted:
            visible_attempts: Dict[str, bool] = {}
            for artifact in artifacts:
                if artifact.attempt_id and artifact.attempt_id not in visible_attempts:
                    try:
                        status = str(self.attempt(artifact.attempt_id).get("status", ""))
                    except WorkspaceError:
                        status = ""
                    visible_attempts[artifact.attempt_id] = status in {"committed", "finalized"}
            artifacts = [
                artifact
                for artifact in artifacts
                if artifact.committed
                and (not artifact.attempt_id or visible_attempts.get(artifact.attempt_id, False))
            ]
        return [
            artifact
            for artifact in artifacts
            if (include_uncommitted or artifact.committed) and (kind is None or artifact.kind == kind)
        ]

    def artifact_by_id(self, artifact_id: str) -> Artifact:
        for artifact in self.list_artifacts():
            if artifact.id == artifact_id:
                return artifact
        raise WorkspaceError(f"Artifact not found: {artifact_id}")

    def read_artifact_text(self, artifact: Artifact) -> str:
        return self.read_artifact_bytes(artifact).decode("utf-8")

    def read_artifact_bytes(self, artifact: Artifact) -> bytes:
        content = self._safe_path(artifact.path).read_bytes()
        if artifact.sha256 and hashlib.sha256(content).hexdigest() != artifact.sha256:
            raise WorkspaceError(f"Artifact integrity check failed: {artifact.id}")
        return content

    def artifact_path(self, artifact: Artifact) -> Path:
        path = self._safe_path(artifact.path)
        if artifact.sha256 and hashlib.sha256(path.read_bytes()).hexdigest() != artifact.sha256:
            raise WorkspaceError(f"Artifact integrity check failed: {artifact.id}")
        return path

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
        return self._write_artifact_bytes(
            filename,
            content.encode("utf-8"),
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
        return self._write_artifact_bytes(
            filename,
            json.dumps(value, ensure_ascii=False, indent=2).encode("utf-8"),
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
        return self._write_artifact_bytes(
            filename,
            content,
            kind=kind,
            description=description,
            source_url=source_url,
            metadata=metadata,
        )

    def _write_artifact_bytes(
        self,
        filename: str,
        content: bytes,
        *,
        kind: str,
        description: str,
        source_url: Optional[str],
        metadata: Optional[Dict[str, Any]],
    ) -> Artifact:
        version = self._active_attempt_id or uuid4().hex
        version_root = self._safe_path(Path("artifacts") / "versions" / version)
        requested = Path(filename)
        if requested.is_absolute():
            raise WorkspaceError("Artifact filename must be relative.")
        target = (version_root / requested).resolve()
        if target == version_root or version_root not in target.parents:
            raise WorkspaceError("Artifact filename must stay inside its version directory.")
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists():
            target = target.with_name(f"{uuid4().hex[:12]}-{target.name}")
        self._atomic_write_bytes(target, content)
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
            sha256=hashlib.sha256(target.read_bytes()).hexdigest(),
            size_bytes=target.stat().st_size,
            step_id=self._active_step_id,
            attempt_id=self._active_attempt_id,
            committed=self._active_attempt_id is None,
        )
        artifacts = [item.to_dict() for item in self.list_artifacts(include_uncommitted=True)]
        artifacts.append(artifact.to_dict())
        self._write_json("artifacts.json", artifacts)
        self.append_log("artifact_written", {"artifact_id": artifact.id, "kind": kind, "path": artifact.path})
        return artifact

    def begin_attempt(
        self,
        step_id: str,
        approval_fingerprint: str,
        recovery_strategy: str = "unknown",
    ) -> str:
        attempt_id = uuid4().hex
        self._active_step_id = step_id
        self._active_attempt_id = attempt_id
        idempotency_key = hashlib.sha256(
            f"{self.root.name}:{step_id}:{approval_fingerprint}".encode("utf-8")
        ).hexdigest()
        self._write_json(
            Path("attempts") / f"{attempt_id}.json",
            {
                "attempt_id": attempt_id,
                "step_id": step_id,
                "approval_fingerprint": approval_fingerprint,
                "idempotency_key": idempotency_key,
                "recovery_strategy": recovery_strategy,
                "status": "prepared",
                "prepared_at": utc_now(),
            },
        )
        return attempt_id

    def start_attempt(self, attempt_id: str) -> Dict[str, Any]:
        record = self.attempt(attempt_id)
        if record.get("status") != "prepared":
            raise WorkspaceError(f"Attempt cannot start from status {record.get('status')!r}.")
        record.update({"status": "running", "started_at": utc_now()})
        self._write_json(Path("attempts") / f"{attempt_id}.json", record)
        self._active_step_id = str(record["step_id"])
        self._active_attempt_id = attempt_id
        return record

    def end_attempt_context(self) -> None:
        self._active_step_id = None
        self._active_attempt_id = None

    def attempt(self, attempt_id: str) -> Dict[str, Any]:
        value = self._read_json(Path("attempts") / f"{attempt_id}.json")
        if not isinstance(value, dict):
            raise WorkspaceError(f"Attempt record is missing: {attempt_id}")
        return value

    def commit_attempt(self, attempt_id: str, summary: str) -> List[Artifact]:
        artifacts = self.list_artifacts(include_uncommitted=True)
        record = self.attempt(attempt_id)
        if record.get("status") != "running":
            raise WorkspaceError(f"Attempt cannot commit from status {record.get('status')!r}.")
        selected = [artifact for artifact in artifacts if artifact.attempt_id == attempt_id]
        manifest = [self._artifact_commit_entry(artifact) for artifact in selected]
        for artifact in selected:
            self._verify_artifact_file(artifact)
        record.update(
            {
                "status": "committed",
                "committed_at": utc_now(),
                "summary": summary,
                "artifact_ids": [artifact.id for artifact in selected],
                "artifacts": manifest,
            }
        )
        # The durable commit marker is written before artifacts become visible.
        self._write_json(Path("attempts") / f"{attempt_id}.json", record)
        return self.finalize_attempt_artifacts(attempt_id)

    @staticmethod
    def _artifact_commit_entry(artifact: Artifact) -> Dict[str, Any]:
        value = artifact.to_dict()
        value.pop("committed", None)
        return value

    def _verify_artifact_file(self, artifact: Artifact) -> None:
        path = self._safe_path(artifact.path)
        if not path.is_file():
            raise WorkspaceError(f"Attempt artifact is missing: {artifact.id}")
        content = path.read_bytes()
        if len(content) != artifact.size_bytes or hashlib.sha256(content).hexdigest() != artifact.sha256:
            raise WorkspaceError(f"Attempt artifact integrity check failed: {artifact.id}")

    def finalize_attempt_artifacts(self, attempt_id: str) -> List[Artifact]:
        record = self.attempt(attempt_id)
        if record.get("status") not in {"committed", "finalized"}:
            raise WorkspaceError("Only a durably committed attempt can expose artifacts.")
        manifest = record.get("artifacts")
        if not isinstance(manifest, list):
            raise WorkspaceError("Committed attempt is missing its artifact manifest.")
        artifacts = self.list_artifacts(include_uncommitted=True)
        by_id = {artifact.id: artifact for artifact in artifacts}
        if len(by_id) != len(artifacts):
            raise WorkspaceError("Artifact registry contains duplicate identifiers.")
        committed: List[Artifact] = []
        for item in manifest:
            if not isinstance(item, dict) or not item.get("id"):
                raise WorkspaceError("Committed attempt has an invalid artifact manifest.")
            artifact = by_id.get(str(item["id"]))
            if artifact is None or artifact.attempt_id != attempt_id:
                raise WorkspaceError(f"Committed attempt artifact is missing or belongs elsewhere: {item.get('id')}")
            if self._artifact_commit_entry(artifact) != dict(item):
                raise WorkspaceError(f"Committed attempt artifact metadata changed: {artifact.id}")
            self._verify_artifact_file(artifact)
            artifact.committed = True
            committed.append(artifact)
        self._write_json("artifacts.json", [artifact.to_dict() for artifact in artifacts])
        return committed

    def mark_attempt_finalized(self, attempt_id: str) -> None:
        record = self.attempt(attempt_id)
        if record.get("status") == "finalized":
            self.finalize_attempt_artifacts(attempt_id)
            return
        if record.get("status") != "committed":
            raise WorkspaceError("Only a committed attempt can be finalized.")
        self.finalize_attempt_artifacts(attempt_id)
        record.update({"status": "finalized", "finalized_at": utc_now()})
        self._write_json(Path("attempts") / f"{attempt_id}.json", record)

    def finish_attempt(self, attempt_id: str, status: str, error: str = "") -> None:
        record = self.attempt(attempt_id)
        record.update({"status": status, "finished_at": utc_now(), "error": error})
        self._write_json(Path("attempts") / f"{attempt_id}.json", record)

    def artifacts_for_attempt(self, attempt_id: str, *, committed_only: bool = True) -> List[Artifact]:
        return [
            artifact
            for artifact in self.list_artifacts(include_uncommitted=not committed_only)
            if artifact.attempt_id == attempt_id and (artifact.committed or not committed_only)
        ]

    def export_archive(self, destination: Path) -> Path:
        import shutil

        destination = destination.expanduser().resolve()
        destination.parent.mkdir(parents=True, exist_ok=True)
        base_name = str(destination.with_suffix(""))
        archive = shutil.make_archive(base_name, "zip", root_dir=self.root)
        return Path(archive)

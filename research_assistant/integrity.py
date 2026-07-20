from __future__ import annotations

import hashlib
import json
from typing import Any, Dict, Mapping, Tuple

from .models import PlanStep, TaskPlan, TaskSpec
from .workspace import TaskWorkspace, WorkspaceError


def canonical_json(value: Any) -> bytes:
    """Serialize approval data deterministically for hashing."""
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def approval_manifest(
    workspace: TaskWorkspace,
    task: TaskSpec,
    plan: TaskPlan,
    step: PlanStep,
    execution_environment: Mapping[str, Any] | None = None,
) -> Dict[str, Any]:
    environment = dict(
        execution_environment
        if execution_environment is not None
        else plan.metadata.get("execution_environment") or {}
    )
    workflow_metadata = {
        key: value
        for key, value in plan.metadata.items()
        if key != "execution_environment"
    }
    visible_artifacts = []
    for position, artifact in enumerate(workspace.list_artifacts()):
        path = workspace._safe_path(artifact.path)
        if not path.is_file():
            raise WorkspaceError(f"Visible artifact is missing: {artifact.id}")
        content = path.read_bytes()
        visible_artifacts.append(
            {
                "position": position,
                "id": artifact.id,
                "kind": artifact.kind,
                "path": artifact.path,
                "description": artifact.description,
                "created_at": artifact.created_at,
                "source_url": artifact.source_url,
                "metadata": artifact.metadata,
                "sha256": hashlib.sha256(content).hexdigest(),
                "size_bytes": len(content),
                "step_id": artifact.step_id,
                "attempt_id": artifact.attempt_id,
                "committed": artifact.committed,
            }
        )
    return {
        "schema": "approval-execution-manifest/v1",
        "task": {
            "id": task.id,
            "goal": task.goal,
            "workflow": task.workflow,
            "provider_name": task.provider_name,
            "urls": list(task.urls),
            "input_files": list(task.input_files),
            "options": task.options,
        },
        "step": {
            "id": step.id,
            "description": step.description,
            "call": step.call.to_dict(),
        },
        "workflow_metadata": workflow_metadata,
        "execution_environment": environment,
        "inputs": workspace.verify_input_snapshots(task),
        # Artifact order is semantic today: several built-in tools consume the
        # latest artifact of a kind, so it must be part of the approval.
        "visible_artifacts": visible_artifacts,
    }


def fingerprint_manifest(manifest: Mapping[str, Any]) -> str:
    return hashlib.sha256(canonical_json(dict(manifest))).hexdigest()


def build_approval_fingerprint(
    workspace: TaskWorkspace,
    task: TaskSpec,
    plan: TaskPlan,
    step: PlanStep,
    execution_environment: Mapping[str, Any] | None = None,
) -> Tuple[Dict[str, Any], str]:
    manifest = approval_manifest(workspace, task, plan, step, execution_environment)
    return manifest, fingerprint_manifest(manifest)

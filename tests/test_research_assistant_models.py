from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from research_assistant.models import Approval, TaskSpec
from research_assistant.settings import AgentSettings, ProviderConfig, SettingsError, load_settings, save_settings
from research_assistant.workspace import TaskWorkspace, WorkspaceError


class ResearchAssistantModelTests(unittest.TestCase):
    def test_approval_round_trip(self) -> None:
        approval = Approval(task_id="task-1", action="approved", step_id="step-01", details={"risk": "read"})
        self.assertEqual(Approval.from_dict(approval.to_dict()).step_id, "step-01")

    def test_workspace_keeps_artifacts_inside_task_directory(self) -> None:
        with TemporaryDirectory() as temp:
            root = Path(temp) / "tasks"
            task = TaskSpec.create("summarize", "file_report", None, [], [])
            workspace = TaskWorkspace.create(root, task)
            artifact = workspace.write_text_artifact(
                "note.md",
                "# hello\n",
                kind="note",
                description="test note",
            )
            self.assertEqual(workspace.read_artifact_text(artifact), "# hello\n")
            with self.assertRaises(WorkspaceError):
                TaskWorkspace.open(root, "../outside")

    def test_settings_never_accepts_plaintext_api_key(self) -> None:
        with self.assertRaises(SettingsError):
            ProviderConfig.from_dict(
                "bad",
                {
                    "kind": "gemini",
                    "model": "gemini-2.5-flash",
                    "api_key": "not-allowed",
                },
            )

    def test_settings_round_trip_keeps_only_secret_reference(self) -> None:
        with TemporaryDirectory() as temp:
            path = Path(temp) / "agent.yaml"
            settings = AgentSettings(
                default_provider="demo",
                providers={
                    "demo": ProviderConfig(
                        name="demo",
                        kind="openai_compatible",
                        model="demo-model",
                        secret_ref="provider:demo",
                        base_url="https://example.com/v1",
                    )
                },
            )
            save_settings(settings, path)
            text = path.read_text(encoding="utf-8")
            self.assertIn("secret_ref: provider:demo", text)
            self.assertNotIn("not-a-real-secret", text)
            self.assertEqual(load_settings(path).providers["demo"].model, "demo-model")

from __future__ import annotations

import io
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from tempfile import TemporaryDirectory

from research_assistant import __version__
from research_assistant.cli import collect_doctor_report, main
from research_assistant.secrets import InMemorySecretStore
from research_assistant.settings import AgentSettings, ProviderConfig, save_settings


class AgentCliTests(unittest.TestCase):
    def test_version_flag_reports_package_version(self) -> None:
        output = io.StringIO()
        with redirect_stdout(output), self.assertRaises(SystemExit) as raised:
            main(["--version"])
        self.assertEqual(raised.exception.code, 0)
        self.assertEqual(output.getvalue().strip(), f"agent {__version__}")

    def test_list_workflows_prints_bundled_workflow_metadata(self) -> None:
        output = io.StringIO()
        with redirect_stdout(output):
            result = main(["list-workflows"])
        self.assertEqual(result, 0)
        text = output.getvalue()
        self.assertIn("document_to_markdown", text)
        self.assertIn("content.prepare_draft", text)

    def test_doctor_report_does_not_expose_credential_value(self) -> None:
        with TemporaryDirectory() as temp:
            config_path = Path(temp) / "agent.yaml"
            settings = AgentSettings(
                providers={
                    "demo": ProviderConfig(
                        name="demo",
                        kind="openai_compatible",
                        model="demo-model",
                        secret_ref="provider:demo",
                        base_url="https://example.test/v1",
                    )
                }
            )
            save_settings(settings, config_path)
            checks, healthy = collect_doctor_report(
                config_path,
                module_probe=lambda _: (True, "installed"),
                chromium_probe=lambda: (True, "local Chromium"),
                secret_store=InMemorySecretStore({"provider:demo": "secret-value-never-printed"}),
            )
        self.assertTrue(healthy)
        details = "\n".join(detail for _, _, detail in checks)
        self.assertIn("credential reference is available", details)
        self.assertNotIn("secret-value-never-printed", details)

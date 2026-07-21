#!/usr/bin/env python3
"""Exercise an installed wheel from outside the source checkout."""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import tempfile
from importlib.metadata import version
from pathlib import Path


EXPECTED_VERSION = "2.1.0.dev0"
EXPECTED_WORKFLOWS = {
    "content_save_draft",
    "crawler_report",
    "document_to_markdown",
    "file_report",
    "research_report",
    "web_to_markdown",
}


def _run(command: list[str], cwd: Path) -> str:
    environment = dict(os.environ)
    environment["PYTHONUTF8"] = "1"
    result = subprocess.run(
        command,
        cwd=cwd,
        env=environment,
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    return result.stdout


def main() -> int:
    import adapters
    import core.spider_engine
    import research_assistant
    import workflows
    from core.spider_engine import GenericSpider
    from research_assistant.workflows import available_workflows

    checkout = os.environ.get("GITHUB_WORKSPACE")
    if checkout:
        checkout_root = Path(checkout).resolve()
        for module in (core.spider_engine, research_assistant):
            if checkout_root in Path(module.__file__).resolve().parents:
                raise RuntimeError(
                    f"Smoke test imported {module.__name__} from the source checkout, not the installed wheel."
                )

    crawler = GenericSpider(
        {
            "name": "installed-crawler-smoke",
            "start_url": "https://example.com/",
            "browser": {"headless": True},
            "fields": [{"name": "title", "selector": "h1"}],
        }
    )
    if (
        crawler.name != "installed-crawler-smoke"
        or crawler.start_urls != ["https://example.com/"]
        or crawler.results != []
    ):
        raise RuntimeError("Installed GenericSpider did not preserve its minimal configuration.")

    metadata_version = version("generic-crawler-research-assistant")
    if metadata_version != EXPECTED_VERSION or research_assistant.__version__ != EXPECTED_VERSION:
        raise RuntimeError(
            f"Version mismatch: metadata={metadata_version}, runtime={research_assistant.__version__}"
        )
    if set(available_workflows()) != EXPECTED_WORKFLOWS:
        raise RuntimeError(f"Bundled workflow mismatch: {available_workflows()}")

    agent = shutil.which("agent")
    if not agent:
        raise RuntimeError("Installed console script 'agent' was not found on PATH.")

    with tempfile.TemporaryDirectory(prefix="agent-package-smoke-") as directory:
        root = Path(directory)
        source = root / "market-notes.csv"
        source.write_text("name,amount\nA,10\nB,20\n", encoding="utf-8")
        workspace_root = root / "tasks"

        if _run([agent, "--version"], root).strip() != f"agent {EXPECTED_VERSION}":
            raise RuntimeError("The installed console script reported an unexpected version.")
        workflow_output = _run([agent, "list-workflows"], root)
        if not EXPECTED_WORKFLOWS.issubset(set(re.findall(r"(?m)^([a-z][a-z0-9_-]+)$", workflow_output))):
            raise RuntimeError("The installed console script did not list every bundled workflow.")

        run_output = _run(
            [
                agent,
                "run",
                "Package installation smoke test",
                "--workflow",
                "file_report",
                "--input",
                str(source),
                "--workspace-root",
                str(workspace_root),
            ],
            root,
        )
        match = re.search(r"(?m)^Task: ([a-f0-9]+)$", run_output)
        if not match:
            raise RuntimeError(f"Could not read the task id from agent output:\n{run_output}")
        task_id = match.group(1)

        for step_id in ("step-01", "step-02", "step-03"):
            _run([agent, "approve", task_id, step_id, "--workspace-root", str(workspace_root)], root)
            _run([agent, "resume", task_id, "--workspace-root", str(workspace_root)], root)

        status_output = _run([agent, "status", task_id, "--workspace-root", str(workspace_root)], root)
        if "Status: completed" not in status_output:
            raise RuntimeError(f"Installed workflow did not complete:\n{status_output}")
        if not (workspace_root / task_id / "artifacts" / "report.md").is_file():
            raise RuntimeError("Installed workflow did not create report.md.")

    print(
        "Installed distribution smoke test passed: "
        f"{research_assistant.__version__}; primary={crawler.__class__.__name__}; "
        f"optional={research_assistant.__name__}; packages={adapters.__name__},{workflows.__name__}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Run the local-only v2.1 verification gates from a source checkout."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _run(arguments: list[str]) -> None:
    environment = dict(os.environ)
    environment.setdefault("PYTHONUTF8", "1")
    print("+", " ".join(arguments), flush=True)
    subprocess.run(
        arguments,
        cwd=ROOT,
        env=environment,
        check=True,
    )


def main() -> int:
    """Verify source code and deterministic fixtures without external traffic."""

    _run(
        [
            sys.executable,
            "-m",
            "compileall",
            "-q",
            "extract_prices.py",
            "agent.py",
            "core",
            "adapters",
            "research_assistant",
            "workflows",
            "scripts",
            "labs",
        ]
    )
    _run([sys.executable, "-m", "unittest", "discover", "-s", "tests", "-v"])
    _run(
        [
            sys.executable,
            "-m",
            "core.crawler_cli",
            "benchmark",
            "--json",
            "--check-baseline",
        ]
    )
    print("Local v2.1 verification passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

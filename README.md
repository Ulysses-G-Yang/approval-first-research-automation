# Approval-First Research Automation

> A local-first toolkit for planning, approving, executing, and tracing research
> workflows over explicit public web pages and user-supplied local files.

[![CI](https://github.com/Ulysses-G-Yang/approval-first-research-automation/actions/workflows/ci.yml/badge.svg)](https://github.com/Ulysses-G-Yang/approval-first-research-automation/actions/workflows/ci.yml)
[![Python 3.12+](https://img.shields.io/badge/python-3.12%2B-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

[中文说明](README.zh-CN.md)

> **Status: Alpha.** The current source tree is a `v2.1.0` candidate. Advanced
> self-healing and platform-adapter modules are experimental, and the latest
> published release remains `v2.0.1`.

![Product preview](docs/assets/product-preview.png)

## Why this project exists

Research automation often forces a poor choice between manual repetition and an
agent that can act without a clear review boundary. This project keeps planning
and execution separate:

1. A deterministic workflow or configured model proposes a plan.
2. The user reviews one explicit step.
3. Only that approved next step may execute.
4. Results, source information, approvals, and logs stay in one local workspace.

The crawler is one controlled tool in this workflow. The project does not claim
unrestricted browser autonomy, anti-bot bypass, or compatibility with arbitrary
websites.

## Current capability status

| Capability | Status | Notes |
| --- | --- | --- |
| Local CSV/JSON/TXT/Markdown reports | **Tested** | Reproducible offline tests. |
| DOCX and text-PDF to Markdown | **Tested** | Scanned pages are preserved; OCR is not included. |
| Offline draft packages | **Tested** | Creates local files only; never uploads or publishes. |
| Public HTTP research | **Limited** | Public-target checks exist; connection-time network hardening is tracked in [#6](https://github.com/Ulysses-G-Yang/approval-first-research-automation/issues/6). |
| Approval checkpoints and audit files | **Limited** | Exact-content binding and recovery are tracked in [#4](https://github.com/Ulysses-G-Yang/approval-first-research-automation/issues/4) and [#5](https://github.com/Ulysses-G-Yang/approval-first-research-automation/issues/5). |
| Configurable browser extraction | **Limited** | Package and browser-network gates are tracked in [#3](https://github.com/Ulysses-G-Yang/approval-first-research-automation/issues/3) and [#6](https://github.com/Ulysses-G-Yang/approval-first-research-automation/issues/6). |
| Page Evolution Lab | **Tested** | Local fixture, not a site-coverage benchmark. |
| Five-layer healing, QualityGate, repair memory | **Experimental** | Modules exist but are not connected to the supported crawler path. |
| E-commerce adapter and domain matching | **Experimental** | Template code, not verified support for 19 sites. |
| OCR and reviewed platform draft saving | **Planned** | Not currently offered. |

The detailed truth table is in [Product Scope](docs/PRODUCT_SCOPE.md), and the
ordered work is in the canonical [Roadmap](ROADMAP.md).

## Install from a source checkout

Python 3.12 is the currently tested runtime.

```bash
git clone https://github.com/Ulysses-G-Yang/approval-first-research-automation.git
cd approval-first-research-automation
python -m venv .venv
```

Activate the environment and install the project:

```bash
# Windows PowerShell
.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -e .

# macOS/Linux
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e .
```

Browser workflows also require a browser binary:

```bash
playwright install chromium
agent doctor
agent list-workflows
```

Until [#3](https://github.com/Ulysses-G-Yang/approval-first-research-automation/issues/3)
is closed, use a source checkout for crawler development; the current wheel does
not yet prove the full crawler package surface.

## Run an offline workflow

Create a task plan from the bundled CSV example:

```bash
agent run "Summarize the market notes" \
  --workflow file_report \
  --input examples/research-report/market-notes.csv
```

The command prints a task ID and the first proposed step. Review it, then execute
one step at a time:

```bash
agent status <TASK_ID>
agent approve <TASK_ID> step-01
agent resume <TASK_ID>
```

Repeat `status`, `approve`, and `resume` until the task completes. Export the
local workspace when it is ready for review:

```bash
agent export <TASK_ID> --output task-export.zip
```

No model provider is required for the deterministic fallback. Provider setup and
model-assisted planning are documented in
[AI Research Assistant](docs/AI_RESEARCH_ASSISTANT.md).

## Supported workflows

| Workflow | Purpose |
| --- | --- |
| `file_report` | Build a traceable report from explicit local inputs. |
| `research_report` | Combine approved public URLs and local sources. |
| `web_to_markdown` | Create a Markdown knowledge package from approved sources. |
| `document_to_markdown` | Convert DOCX, text PDF, Markdown, or text documents. |
| `content_save_draft` | Prepare an offline platform-specific draft package. |
| `crawler_report` | Run a reviewed crawler YAML and compose a local report. |

See [Workflow Authoring](docs/WORKFLOW_AUTHORING.md) and the
[Example Gallery](examples/README.md) for inputs and expected artifacts.

## Safety boundary

- A model may propose only registered tools and declared arguments.
- A tool may read only URLs or local files included in the task.
- Credentials are referenced from the operating-system credential store, not
  embedded in workflow or crawler YAML.
- Assistant crawler YAML rejects scripted actions and plaintext API keys.
- Draft tools create local packages; no built-in tool logs in, uploads, saves a
  platform draft, or publishes content.
- Known approval, recovery, packaging, and browser-network gaps are public in the
  roadmap instead of being described as completed capabilities.

See [Security Policy](SECURITY.md) for responsible reporting.

## Project layout

```text
research_assistant/   planner, approval runner, tools, providers, workspace
workflows/            versioned declarative workflows
core/                 crawler engine and experimental extraction modules
adapters/             experimental adapter interfaces and templates
labs/                 local page-evolution fixtures
examples/             reproducible offline examples
tests/                offline unit and workflow tests
```

The supported browser path is currently:

```text
crawler_report -> browser.extract -> GenericSpider
```

The five-layer `SelfHealingEngine`, `QualityGate`, `RepairPersistence`, and
adapter prototypes are not yet part of that path.

## Development

```bash
python -m unittest discover -s tests -v
python -m compileall -q extract_prices.py agent.py core adapters research_assistant scripts labs
python -m labs.page_evolution.run_lab --json
git diff --check
```

Contribution and release requirements are documented in
[CONTRIBUTING.md](CONTRIBUTING.md) and
[Release Checklist](docs/RELEASE_CHECKLIST.md).

## Historical context

The repository began as an educational Taobao extraction project. Historical
tags remain available, but they are not the current product promise. See
[Educational Version](docs/EDUCATIONAL_VERSION.md).

## License

[MIT](LICENSE)

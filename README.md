# Traceable AI Research Automation

[简体中文](README.zh-CN.md)

> Turn public web pages and local files into cited Markdown, datasets, and review-ready draft packages.
>
> The model proposes the plan. You approve every action.

[![CI](https://github.com/3023345758/Taobao-Anti-Scraping-Project/actions/workflows/ci.yml/badge.svg)](https://github.com/3023345758/Taobao-Anti-Scraping-Project/actions/workflows/ci.yml)
[![Python](https://img.shields.io/badge/Python-3.12-3776AB?logo=python&logoColor=white)](https://www.python.org/)
[![License](https://img.shields.io/badge/License-MIT-2EA44F)](LICENSE)

![Traceable research workflow preview](docs/assets/product-preview.png)

[View the local workflow overview animation](docs/assets/workflow-overview.gif)

## Collect, Verify, Compose

This project is a local, approval-first research and content automation tool. It combines a configurable browser extraction engine with a restricted task runner:

| Stage | What happens | What you keep |
| --- | --- | --- |
| **Collect** | Read explicitly supplied public URLs and local files. | Original inputs and normalized records. |
| **Verify** | Inspect a plan, approve each tool call, and review its target and risk. | `plan.json`, approvals, logs, and source manifests. |
| **Compose** | Produce Markdown reports, datasets, document bundles, or offline draft packages. | Reviewable artifacts that stay inside one task workspace. |

The assistant never receives permission to run arbitrary shell commands, Python, browser JavaScript, log in to websites, or publish content by itself.

## What You Can Do Today

- **Research public sources**: collect explicitly provided pages, deduplicate them, and write a source-backed Markdown report.
- **Turn files into Markdown**: convert DOCX, text PDFs, Markdown, TXT, CSV, and JSON into task-local artifacts. Document images are preserved as local assets; scanned PDFs are retained as rendered images and reported as needing OCR.
- **Prepare content packages**: create an offline Juejin, Zhihu, or CSDN Markdown draft package. Nothing is uploaded, saved to a platform, or published.
- **Use a model without giving it control**: configure OpenAI-compatible endpoints, Gemini, or Qwen through a hidden API-key prompt and the system credential store. The model produces a plan or an optional summary; registered tools do the work after approval.
- **Build developer workflows**: use versioned YAML workflows, a guarded plugin contract, and the existing `GenericSpider` engine.

See the [workflow gallery](docs/WORKFLOW_GALLERY.md), [product scope](docs/PRODUCT_SCOPE.md), and [AI assistant architecture](docs/AI_RESEARCH_ASSISTANT.md).

## Quick Start

Python 3.12 is the verified runtime. The first commands are local and do not require an API key:

```bash
conda env create -f environment.yml
conda activate generic-crawler-py312
pip install -e .
agent doctor
agent list-workflows
```

Run the included offline CSV example. The task ID is printed by the first command; inspect the plan before approving the next step.

```bash
agent run "Summarize the example market notes" \
  --workflow file_report \
  --input examples/research-report/market-notes.csv \
  --workspace-root .demo-tasks

agent approve <task-id> step-01
agent resume <task-id>
agent status <task-id>
```

Each `resume` runs at most one approved step. A completed task contains its report, source manifest, task plan, approval history, and execution log.

## A Clear Contract for Models

Non-developers can configure a provider once and describe a goal in natural language. Developers can use deterministic YAML workflows without a model.

```bash
# Any OpenAI Chat Completions-compatible endpoint.
agent configure provider \
  --name default \
  --kind openai_compatible \
  --model your-model \
  --base-url https://your-endpoint/v1 \
  --make-default

# Native providers are supported too.
agent configure provider --name gemini --kind gemini --model gemini-2.5-flash
agent configure provider --name qwen --kind qwen --model qwen-plus
```

The CLI asks for the API key without echoing it and stores it in the Windows credential store. YAML contains a `secret_ref`, never a plaintext key. Provider configuration supports planning and optional report summarization; it does not grant an LLM arbitrary execution rights.

## Extraction Is an Engine, Not the Product Claim

The included `GenericSpider` is available when you need structured extraction from explicitly approved public sites. It uses three bounded paths:

1. Configured CSS selectors.
2. Scrapling adaptive parsing when a selector returns nothing.
3. An explicitly enabled, page-local LLM selector suggestion as a last fallback.

The third path is off by default, retries only on the current page, and degrades to an empty value with a log when it fails. It is not a promise to recover every changed website.

For a repeatable, offline demonstration of selector drift and fallback behavior, run the [Page Evolution Lab](labs/page_evolution/README.md). It uses local fixtures only and never contacts a third-party site.

## Workspaces and Artifacts

Every task is isolated under `~/GenericCrawler/tasks/<task-id>/` by default:

```text
task.json          Goal and state, without secrets
plan.json          Restricted plan and visible targets
approvals.jsonl    Approval audit trail
run.jsonl          Execution events
artifacts/         Source captures, normalized data, Markdown, and assets
artifacts/report.md
artifacts/sources.jsonl
```

`agent export <task-id>` creates a portable archive of this evidence trail.

## Historical Education Archive

The repository name and the `v1.0-educational` tag preserve an earlier single-site experiment. That tag is immutable historical material, not the active product line. It should not be used to bypass access controls, collect non-public data, or violate a service's terms.

Read [the education archive note](docs/EDUCATIONAL_VERSION.md) for its purpose, limits, and the relationship to the local Page Evolution Lab.

## Project Map

```text
agent.py               Approval-gated assistant CLI
research_assistant/    Plans, providers, task workspaces, and registered tools
workflows/             Versioned declarative workflows
core/                  Configurable browser extraction and selector repair
examples/              Small, reproducible local examples
labs/                  Offline compatibility and teaching labs
docs/                  Scope, architecture, workflows, and launch material
tests/                 Offline unit and integration tests
```

## Safety and Contribution

- Use only public, authorized sources and data you are allowed to process.
- Do not commit API keys, browser profiles, cookies, private page captures, or task output.
- Read [CONTRIBUTING.md](CONTRIBUTING.md) before proposing a workflow or plugin.
- Report sensitive issues through [SECURITY.md](SECURITY.md), not public issues.
- See [ROADMAP.md](ROADMAP.md) for the next reviewed extensions.

## License

MIT

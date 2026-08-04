# JS-Adaptive Generic Crawler

> JS 自适应通用爬虫 / JS-Adaptive Generic Crawler

> A source-checkout-first, YAML-configured Playwright crawler for public pages
> you are permitted to access, with local JSON, JSONL, and CSV output. An
> approval-first research assistant is included as an optional orchestration
> layer.

[![CI](https://github.com/Ulysses-G-Yang/approval-first-research-automation/actions/workflows/ci.yml/badge.svg)](https://github.com/Ulysses-G-Yang/approval-first-research-automation/actions/workflows/ci.yml)
[![Python 3.12+](https://img.shields.io/badge/python-3.12%2B-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

[中文说明](README.zh-CN.md)

> **Status: Alpha.** `v2.1.0` is the release source for the locally tested
> deterministic repair pipeline. Model quality remains Experimental, the
> e-commerce Adapter remains Limited, and this is not a broad real-site
> compatibility claim.

## What this project is

The primary product is `GenericSpider`: a configurable crawler that turns a
trusted YAML definition into structured local records. A configuration can
declare authorized start URLs, browser settings, pagination, page or item
selectors, and output fields without hard-coding one site into the engine.

The repository also contains an optional approval-first assistant. It can wrap
the crawler and local document tools in reviewed, traceable workflows, but it is
not required to run the crawler directly.

The project does not claim compatibility with arbitrary websites, anti-bot or
access-control bypass, undetectable automation, or guaranteed selector healing.

## Current capability status

| Layer | Capability | Status | Current boundary |
| --- | --- | --- | --- |
| Core crawler | YAML-configured Playwright extraction | **Limited** | Local and installed-package tests cover the supported path; this is not a broad real-site compatibility claim. |
| Core crawler | Configured CSS field extraction | **Tested** | Reproducible local HTML fixtures cover successful and failed selectors. |
| Core crawler | Pagination and JSON/JSONL/CSV output | **Limited** | Implemented, but there is no published cross-site compatibility benchmark. |
| Core crawler | Unified extraction pipeline | **Tested** | `configured → fallback → approved history → Scrapling → optional LLM → empty`; every candidate uses one QualityGate. |
| Core crawler | Embedded and passive network JSON captures | **Limited** | Bounded local fixtures cover embedded state and GET 2xx XHR/fetch JSON; no arbitrary live-site claim. |
| Core crawler | JS Evolution Benchmark | **Tested** | Seven deterministic synthetic evolution families, zero external network/model calls. |
| Core crawler | RepairEpisode v1 and Experience Store | **Tested** | Explicit opt-in SQLite index plus local SHA-256 CAS; disabled runs create nothing. |
| Core crawler | Explicitly selected e-commerce Adapter candidate rules | **Limited** | Activated only through `GenericSpider.from_adapter(...)` and verified against one owned synthetic fixture; domain strings are not a support list. |
| Optional assistant | Local CSV/JSON/TXT/Markdown reports | **Tested** | Reproducible offline workflows. |
| Optional assistant | DOCX and text-PDF to Markdown | **Tested** | Scanned pages are preserved; OCR is not included. |
| Optional assistant | Approval-bound execution and recovery | **Limited** | Fingerprints, process locks, crash recovery, and versioned artifacts are tested for current workspaces; legacy workspaces are view/export-only, and interrupted remote or model calls require review. |
| Optional assistant | Public HTTP and reviewed browser access | **Limited** | Exact hosts, public DNS, methods, request counts, and run time are checked; connection pinning and aggregate browser bytes are follow-up hardening in [#14](https://github.com/Ulysses-G-Yang/approval-first-research-automation/issues/14), and this is not described as a network sandbox. |
| Optional assistant | Offline draft packages | **Tested** | Creates local files only; never uploads or publishes. |

The detailed truth table is in [Product Scope](docs/PRODUCT_SCOPE.md), and the
ordered work is in the canonical [Roadmap](ROADMAP.md).

## Run the crawler from a source checkout

Python 3.12 is the currently tested runtime.

```bash
git clone https://github.com/Ulysses-G-Yang/approval-first-research-automation.git
cd approval-first-research-automation
python -m venv .venv

# Windows PowerShell
.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -e .

# macOS/Linux
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e .
```

Install Chromium for browser extraction:

```bash
playwright install chromium
```

Create a trusted `crawler.yaml` for a public page you are allowed to access:

```yaml
name: public-page-example
start_url: https://example.com/
browser:
  headless: true
request:
  wait_until: domcontentloaded
pagination:
  enabled: false
fields:
  - name: title
    selector: h1
  - name: url
    source: page_url
    scope: page
```

Run the installed crawler entry point:

```bash
crawler run --config crawler.yaml --output output/records.json
```

`python extract_prices.py --config ...` remains a compatibility wrapper.

Optional JavaScript data captures extend old YAML without invalidating it:

```yaml
captures:
  - name: bootstrap
    type: embedded_json
    selector: script#page-state
    required: true
    max_bytes: 1048576
  - name: catalog
    type: network_json
    url_glob: "https://example.com/api/catalog*"
    required: false
    max_bytes: 1048576
fields:
  - name: title
    source: bootstrap.props.items.0.title
```

`network_json` is passive: it records only the first matching GET, 2xx,
XHR/fetch JSON response already produced by the page. It does not initiate a
new endpoint request.

Direct crawler configurations are trusted code-like input: the standalone
surface supports browser launch/context options and optional JavaScript actions.
Use only configurations you control and targets you are authorized to access.

Run the v2.1 release benchmark entirely offline:

```bash
crawler benchmark --json --check-baseline
```

The benchmark replays checked-in synthetic DOM, embedded state, network JSON,
and hydration fixtures twice. It is a regression gate, not evidence of broad
website support.

Repair episodes are also opt-in. Without `--experience-store`, no database or
user-directory file is created:

```bash
crawler run --config crawler.yaml --output output/records.json \
  --experience-store output/experience.sqlite3
crawler episodes list --store output/experience.sqlite3 --json
crawler episodes show <EPISODE_ID> --store output/experience.sqlite3 --json
crawler episodes export <EPISODE_ID> --store output/experience.sqlite3 --json
```

Non-synthetic content is structure-only by default. For an explicitly
authorized source, `--retain-full-episode-content` opts into redacted full
text/JSON capture; it is rejected for `public` or `unknown` authorization.

See [JS-Adaptive Crawler](docs/JS_ADAPTIVE_CRAWLER.md) for capture, privacy,
repair, and promotion boundaries.

The `v2.1.0` distribution is delivered through a GitHub Release containing one
wheel, one source distribution, and `SHA256SUMS.txt`; it is not published to
PyPI. The wheel contains `core`, `adapters`, `research_assistant`, and bundled
workflows. CI installs it outside the checkout on Windows and Linux, and source
checkout installation remains supported.

## Optional approval-first assistant

![Optional assistant preview](docs/assets/product-preview.png)

The optional `agent` command adds reviewed steps, local task workspaces,
artifacts, and audit information around crawler, web, file, and document tools.

```bash
agent doctor
agent list-workflows
agent run "Summarize the market notes" \
  --workflow file_report \
  --input examples/research-report/market-notes.csv
```

The command prints a task ID and the first proposed step. Review and execute one
step at a time:

```bash
agent status <TASK_ID>
agent approve <TASK_ID> step-01
agent resume <TASK_ID>
```

No model provider is required for deterministic workflows. Provider setup and
model-assisted planning are documented in
[AI Research Assistant](docs/AI_RESEARCH_ASSISTANT.md).

### Optional assistant workflows

| Workflow | Purpose |
| --- | --- |
| `crawler_report` | Run a reviewed crawler YAML and compose a local report. |
| `file_report` | Build a traceable report from explicit local inputs. |
| `research_report` | Combine approved public URLs and local sources. |
| `web_to_markdown` | Create a Markdown knowledge package from approved sources. |
| `document_to_markdown` | Convert DOCX, text PDF, Markdown, or text documents. |
| `content_save_draft` | Prepare an offline platform-specific draft package. |

See [Workflow Authoring](docs/WORKFLOW_AUTHORING.md) and the
[Example Gallery](examples/README.md) for inputs and expected artifacts.

## Safety boundaries

### Direct crawler

- Runs a user-supplied, trusted YAML configuration without per-step approval.
- May use explicitly configured browser options and JavaScript actions.
- Must be used only on pages the operator is authorized to access.
- Is not a network sandbox and does not promise access-control or anti-bot bypass.

### Optional assistant

- A model may propose only registered tools and declared arguments.
- Tools may read only URLs or local files included in the task.
- Credentials are referenced from the operating-system credential store, not
  embedded in workflow or assistant crawler YAML.
- Assistant crawler YAML rejects scripted actions and plaintext API keys.
- Draft tools create local packages; no built-in tool logs in, uploads, saves a
  platform draft, or publishes content.

Assistant-mode web tools add strict crawler configuration, approved-host checks,
and browser request interception as application-layer defense in depth. This is
not a complete network sandbox; DNS rebinding remains tracked in
[Security Policy](SECURITY.md). Direct trusted-config `GenericSpider` runs are
outside this approval boundary.

See [Security Policy](SECURITY.md) for responsible reporting.

## Project layout

```text
core/spider_engine.py   primary GenericSpider engine
core/crawler_cli.py     installed crawler CLI
extract_prices.py       legacy-compatible source wrapper
configs/                crawler configuration templates
labs/                   local page-evolution fixtures
adapters/               experimental adapter interfaces and templates
research_assistant/     optional planner, approval runner, tools, providers
workflows/              optional versioned assistant workflows
examples/               reproducible offline examples
tests/                  crawler and workflow tests
```

The two supported layers are separate:

```text
trusted YAML -> crawler / extract_prices.py -> GenericSpider
             -> unified extraction + optional captures -> JSON/JSONL/CSV

reviewed task -> agent run --workflow crawler_report -> browser.extract -> GenericSpider
```

`SelfHealingEngine` is now a compatibility facade over the same extraction
pipeline used by `GenericSpider`; `QualityGate`, explicitly enabled approved
repair memory, and RepairEpisode evidence are on that path. Adapters are never
selected by domain implicitly: callers opt in through `GenericSpider.from_adapter(...)`.

## Development

```bash
python -m unittest discover -s tests -v
python -m compileall -q extract_prices.py agent.py core adapters research_assistant workflows scripts labs
crawler benchmark --json --check-baseline
git diff --check
```

Contribution and release requirements are documented in
[CONTRIBUTING.md](CONTRIBUTING.md) and
[Release Checklist](docs/RELEASE_CHECKLIST.md).

## Historical context

The repository began as an educational Taobao extraction project. The active
line generalizes the crawler engine and retains a no-default-target compatibility
example; immutable historical behavior remains on the old tags. Neither is a
production claim. See
[Educational Version](docs/EDUCATIONAL_VERSION.md).

## License

[MIT](LICENSE)

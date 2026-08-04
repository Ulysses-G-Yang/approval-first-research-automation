# JS-Adaptive Generic Crawler Product Scope

## Product promise

The primary product is a local, configuration-driven crawler for public pages
the operator is permitted to access.

```text
trusted crawler YAML + permitted public pages
    -> GenericSpider
    -> local JSON, JSONL, or CSV records
```

An optional approval-first assistant can wrap the crawler and local file or
document tools in reviewed workflows:

```text
explicit URLs and local files
    -> reviewed assistant plan
    -> registered crawler, web, file, or document tools
    -> local reports, manifests, artifacts, and logs
```

The assistant is not required to run `GenericSpider`, and its stricter approval
boundary does not redefine the trusted-configuration surface of the standalone
crawler.

## Status vocabulary

- **Tested** — reachable through the stated supported path and covered by reproducible tests.
- **Limited** — reachable, but an important installation, runtime, or compatibility boundary is not yet verified.
- **Experimental** — reachable only by explicit opt-in, but its quality or
  compatibility evidence is not strong enough for a support claim.
- **Planned** — not implemented.

## Capability truth table

### Core crawler

| Capability | Status | Current boundary |
| --- | --- | --- |
| `GenericSpider` with trusted YAML configuration | Limited | Windows/Linux wheel tests cover the installed engine, and controlled local Chromium fixtures cover network/capture boundaries; real-target compatibility is not benchmarked. |
| Configured CSS field extraction | Tested | Covered by local HTML fixtures for successful and failed selectors. |
| Page/list extraction and pagination | Limited | Implemented, but not measured across a published target corpus. |
| JSON, JSONL, and CSV output | Tested | The installed `crawler run` command and old wrapper share the output implementation. |
| Unified extraction pipeline | Tested | GenericSpider and SelfHealingEngine share configured/fallback/approved history/Scrapling/optional LLM ordering and QualityGate. |
| Optional LLM selector repair | Experimental | Disabled by default and tested with controlled doubles, not a model-quality benchmark. |
| Embedded/network JSON capture | Limited | Local synthetic fixtures cover bounded inline JSON and passive GET 2xx XHR/fetch JSON. |
| JS Evolution Benchmark | Tested | Seven local evolution families with deterministic artifact and stage gates; not a target-site support claim. |
| RepairEpisode v1 / Experience Store | Tested | Explicit local SQLite + CAS path, privacy policy, legacy import, and CLI inspection/export are covered. |
| Explicitly selected e-commerce Adapter | Limited | Enabled only through `GenericSpider.from_adapter(...)` and tested with one owned synthetic fixture; domain strings are candidates, not verified site support. |

### Optional assistant

| Capability | Status | Current boundary |
| --- | --- | --- |
| Local CSV/JSON/TXT/Markdown reports | Tested | Reads only files explicitly supplied with `--input`. |
| DOCX and text-PDF conversion | Tested | Scanned pages are preserved for review; OCR is not performed. |
| Offline draft packages | Tested | Produces local files only; no login, upload, save, or publication. |
| Public HTTP page reading | Limited | Exact approved hosts and public DNS answers are checked per request/redirect; connection pinning and aggregate browser bytes are tracked in [#14](https://github.com/Ulysses-G-Yang/approval-first-research-automation/issues/14), and no network-sandbox claim is made. |
| Approval-bound execution and recovery | Limited | Fingerprints, process locks, crash recovery, and versioned artifacts are tested for current workspaces; legacy workspaces are view/export-only, and interrupted remote/model calls require review. |
| Reviewed browser extraction | Limited | Agent mode has a strict config and subrequest policy; standalone trusted configuration and network-layer isolation remain outside this boundary. |
| OCR | Planned | No OCR is currently performed. |
| Platform draft save or formal publish | Planned | Requires a reviewed adapter and a separate sensitive approval. |

## Trust boundaries

### Direct crawler

- Treats its user-supplied YAML as trusted, code-like input.
- May use configured browser launch/context settings and JavaScript actions.
- Accepts legacy `browser.stealth` as a no-op; detection-evasion behavior is not provided.
- Does not provide per-request approval or a hardened network sandbox.
- Must be operated only against targets the user is authorized to access.
- The default LLM example is local Ollama. Explicit Gemini/Qwen configuration
  sends a truncated page context to that remote provider and requires authorized
  input plus operator consent; neither provider is required for crawler use.

### Optional assistant

- Plans may use only registered tools and declared arguments.
- Local tools may read only explicit inputs and write only inside the task workspace.
- Credentials stay in the operating-system credential store and are referenced by name.
- Assistant browser YAML rejects scripted actions and plaintext API keys.
- Stronger claims remain Limited until their linked release gates are implemented and tested.

## Claims we do not make

- “Scrapes any website,” “supports 19 platforms,” or “self-heals every selector.”
- “The standalone crawler is an approval or network sandbox.”
- “Every browser subrequest is already individually approved.”
- “CI passing means production-ready.”
- “Fully automatic publishing,” “anti-bot bypass,” or “undetectable crawling.”
- Research novelty, paper acceptance, or model-quality improvements without experiments.

## v2.1 non-goals and data boundary

- No model-authored overwrite of Python source or an existing crawler configuration.
- No LLM/reviewer judgment as proof of correctness; deterministic replay and QualityGate decide.
- No daemon, external database, web dashboard, or PyPI publication.
- No credentials, browser profiles, local/session storage, private sessions, or unauthorized page data.
- No access-control, CAPTCHA, anti-bot, risk-control, or detection-mechanism evasion.

## Release policy

- Existing historical tags remain immutable.
- `v2.0.1` remains the latest release until every v2.1.0 gate in the canonical roadmap is complete.
- A **Tested** label must name the tested surface. Installed-package support is claimed only after installation and regression evidence exists.
- Release tags, package versions, release notes, and GitHub assets must point to the same commit.

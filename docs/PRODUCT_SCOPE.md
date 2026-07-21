# Generic Crawler Product Scope

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
- **Experimental** — prototype or parallel module that is not part of the supported path.
- **Planned** — not implemented.

## Capability truth table

### Core crawler

| Capability | Status | Current boundary |
| --- | --- | --- |
| `GenericSpider` with trusted YAML configuration | Limited | Runs from a source checkout; installed distribution coverage is tracked in #3. |
| Configured CSS field extraction | Tested | Covered by local HTML fixtures for successful and failed selectors. |
| Page/list extraction and pagination | Limited | Implemented, but not measured across a published target corpus. |
| JSON, JSONL, and CSV output | Limited | Implemented by the source-checkout CLI; installed CLI behavior is not yet a release contract. |
| Adaptive fallback control path | Limited | A deterministic stand-in exercises the control path; real Scrapling recovery is not benchmarked. |
| Optional LLM selector repair | Experimental | Disabled by default and tested with controlled doubles, not a model-quality benchmark. |
| Page Evolution Lab | Tested | A local regression fixture, not a target-site support claim. |
| Five-layer self-healing pipeline | Experimental | Prototype modules are not connected to `GenericSpider`. |
| QualityGate and RepairPersistence | Experimental | Used by a prototype path, not the supported crawler path. |
| E-commerce adapter | Experimental | One generic template; domain strings are matching candidates, not verified site support. |

### Optional assistant

| Capability | Status | Current boundary |
| --- | --- | --- |
| Local CSV/JSON/TXT/Markdown reports | Tested | Reads only files explicitly supplied with `--input`. |
| DOCX and text-PDF conversion | Tested | Scanned pages are preserved for review; OCR is not performed. |
| Offline draft packages | Tested | Produces local files only; no login, upload, save, or publication. |
| Public HTTP page reading | Limited | Redirects are checked, but connection-time DNS enforcement is tracked in #6. |
| Approval checkpoints and local logs | Limited | Exact-content binding and crash safety are tracked in #4 and #5. |
| Reviewed browser extraction | Limited | The assistant accepts a narrower crawler configuration than standalone `GenericSpider`; package and browser-network gates are tracked in #3 and #6. |
| OCR | Planned | No OCR is currently performed. |
| Platform draft save or formal publish | Planned | Requires a reviewed adapter and a separate sensitive approval. |

## Trust boundaries

### Direct crawler

- Treats its user-supplied YAML as trusted, code-like input.
- May use configured browser launch/context settings and JavaScript actions.
- Does not provide per-request approval or a hardened network sandbox.
- Must be operated only against targets the user is authorized to access.

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

## Release policy

- Existing historical tags remain immutable.
- `v2.0.1` remains the latest release until every v2.1.0 gate in the canonical roadmap is complete.
- A **Tested** label must name the tested surface. Installed-package support is claimed only after installation and regression evidence exists.
- Release tags, package versions, release notes, and GitHub assets must point to the same commit.

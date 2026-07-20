# Product Scope

## Product promise

Approval-First Research Automation is a local-first toolkit for planning,
approving, executing, and tracing research workflows over explicit public web
pages and user-supplied local files.

```text
Public web pages + local files
    -> reviewed task plan
    -> explicit approval checkpoints
    -> local Markdown, datasets, manifests, and logs
```

The crawler is one controlled tool inside this product. It is not the product's
identity and it is not an unrestricted browser agent.

## Status vocabulary

- **Tested** — reachable through the supported path and covered by reproducible tests.
- **Limited** — reachable, but an important boundary or installation scenario is not yet verified.
- **Experimental** — prototype or parallel module that is not part of the supported path.
- **Planned** — not implemented.

## Capability truth table

| Capability | Status | Current boundary |
| --- | --- | --- |
| Local CSV/JSON/TXT/Markdown reports | Tested | Reads only files explicitly supplied with `--input`. |
| DOCX and text-PDF conversion | Tested | Scanned pages are preserved for review; OCR is not performed. |
| Offline draft packages | Tested | Produces local files only; no login, upload, save, or publication. |
| Public HTTP page reading | Limited | Redirects are checked, but connection-time DNS enforcement is tracked in #6. |
| Approval checkpoints and local logs | Limited | Steps require approval; exact-content binding and crash safety are tracked in #4 and #5. |
| Configurable Playwright extraction | Limited | Source-checkout path exists; installed-package and full browser-network checks are tracked in #3 and #6. |
| Configured selector plus adaptive fallback | Limited | Covered by local fixtures, not by a published compatibility benchmark. |
| Page Evolution Lab | Tested | A local regression fixture, not a target-site support claim. |
| Five-layer self-healing pipeline | Experimental | Prototype modules are not connected to `GenericSpider`/`browser.extract`. |
| QualityGate and RepairPersistence | Experimental | Used by the prototype path, not the supported workflow. |
| E-commerce adapter | Experimental | A generic template; the listed domains are matching candidates, not verified support. |
| OCR | Planned | No OCR is currently performed. |
| Platform draft save or formal publish | Planned | Requires a reviewed adapter and a separate sensitive approval. |

## Supported trust boundary

- Plans may use only registered tools and declared arguments.
- Local tools may read only explicit inputs and write only inside the task workspace.
- Credentials stay in the operating-system credential store and are referenced by name.
- Browser YAML used by the assistant rejects scripted actions and plaintext API keys.
- Advanced safety claims remain limited until the linked P0 issues are implemented and tested.

## Claims we do not make

- “Scrapes any website,” “supports 19 platforms,” or “self-heals every selector.”
- “Every browser subrequest is already individually approved.”
- “CI passing means production-ready.”
- “Fully automatic publishing,” “anti-bot bypass,” or “undetectable crawling.”
- Research novelty, paper acceptance, or model-quality improvements without experiments.

## Release policy

- Existing historical tags remain immutable.
- `v2.0.1` remains the latest release until every v2.1.0 gate in the canonical roadmap is complete.
- A feature becomes **Tested** only after installation, supported-path, and regression evidence exists.
- Release tags, package versions, release notes, and GitHub assets must point to the same commit.

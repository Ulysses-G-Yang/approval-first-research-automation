# Product Scope

## Product promise

This repository is an approval-first research and content automation tool for public web sources and explicitly supplied local files. Its output is intended to be inspectable: Markdown, structured data, source manifests, task plans, approval records, and logs stay together in an isolated task workspace.

The public message is deliberately outcome-oriented:

```text
Public web pages + local files -> traceable Markdown, datasets, and review-ready draft packages
```

## Capability truth table

| Capability | Status | Public wording | Important boundary |
| --- | --- | --- | --- |
| Public webpage research | Available | Read approved public sources and create a cited report. | URLs must be explicit or selected from an approved public search result. |
| Local data reports | Available | Read CSV, JSON, TXT, or Markdown and produce a traceable report. | Tools can only read files passed with `--input`. |
| DOCX and PDF conversion | Available | Convert DOCX and text PDFs into Markdown with local image assets. | Scanned PDF pages are rendered and flagged; OCR is not performed. |
| Offline content packages | Available | Prepare Juejin, Zhihu, or CSDN draft packages locally. | No browser, login, upload, platform save, or publication occurs. |
| OpenAI-compatible, Gemini, Qwen providers | Available | Configure a provider through the CLI and secure credential store. | A model can plan or summarize only; it cannot execute arbitrary code. |
| Configurable browser extraction | Available | Extract fields from explicitly approved, public, configuration-defined targets. | The assistant blocks YAML actions and plaintext keys in V1. |
| Adaptive selector fallback | Available | Try an adaptive parser after a configured selector returns no value. | It is a best-effort extraction aid, not an availability guarantee. |
| LLM selector suggestion | Optional | Ask a configured model for one page-local selector candidate. | Disabled by default; no automatic persistent rewrite is made. |
| Page Evolution Lab | Available in this release branch | Replay selector and page-state drift with local fixtures. | It is not a benchmark or a target-site compatibility claim. |
| OCR for scanned PDFs | Roadmap | Not currently offered. | A rendered page image is preserved for later review. |
| Platform draft save or formal publish | Roadmap | Not currently offered by built-in tools. | Requires a reviewed adapter and separate approval. |
| Private-page automation, access-control bypass, arbitrary shell/Python/JS | Not supported | Not a project goal. | These actions are intentionally outside the V1 permission model. |

## Message hierarchy

1. **Primary**: traceable research automation for web pages and files.
2. **Differentiator**: a model proposes a plan, but people approve every action and retain evidence.
3. **Developer extension**: workflows, providers, plugins, and extraction are versioned and reviewable.
4. **Engine detail**: browser extraction, adaptive parsing, and LLM selector suggestions support the workflows; they are not the headline.
5. **Historical context**: the Taobao material is an education archive, not a product promise.

## Claims we do not make

- "Scrapes any website" or "self-heals every selector."
- "Fully automatic publishing" or "one-click multi-platform posting."
- "Anti-bot bypass," "undetectable," or any claim about defeating access controls.
- Performance, coverage, or reliability figures without a committed benchmark and reproduction method.

## Repository and release policy

- Keep the existing repository URL to preserve historical links.
- Keep `v1.0-educational`, `v2.0.0`, and `v2.0.1` immutable.
- Treat the current product-surface branch as a `v2.1.0` candidate until it passes offline validation and is reviewed into the default branch.
- Update the repository-wide About text and Topics only when the default branch actually contains the product claims.

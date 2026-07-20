# Roadmap

This is the canonical roadmap for Approval-First Research Automation.

The repository is currently an **Alpha**. The latest published release remains
`v2.0.1`; the current source tree is a `v2.1.0` candidate and must not be tagged
until the release gates below are complete.

## v2.1.0 trustworthy-alpha gates

- [#3](https://github.com/Ulysses-G-Yang/approval-first-research-automation/issues/3) — build and smoke-test installable wheel and sdist artifacts.
- [#4](https://github.com/Ulysses-G-Yang/approval-first-research-automation/issues/4) — bind approvals to the exact execution manifest.
- [#5](https://github.com/Ulysses-G-Yang/approval-first-research-automation/issues/5) — prevent concurrent execution, recover interrupted tasks, and preserve immutable artifacts.
- [#6](https://github.com/Ulysses-G-Yang/approval-first-research-automation/issues/6) — enforce an explicit public-network boundary for HTTP and browser extraction.
- [#9](https://github.com/Ulysses-G-Yang/approval-first-research-automation/issues/9) — add a gated release workflow after the foundation issues close.

Every gate requires tests that exercise the installed package or a local network
fixture. A green unit-test badge alone is not sufficient evidence for release.

## After the foundation

These are follow-up engineering investigations, not v2.1.0 promises:

- [#7](https://github.com/Ulysses-G-Yang/approval-first-research-automation/issues/7) — replace the parallel selector-repair implementations with one tested extraction pipeline.
- [#8](https://github.com/Ulysses-G-Yang/approval-first-research-automation/issues/8) — establish frozen page-evolution fixtures and measurable baselines.
- Evaluate OCR, additional document formats, and reviewed draft-saving adapters only after the core execution boundary is stable.

## Experimental, not committed

- The five-layer self-healing engine, quality gate, repair memory, and generic
  e-commerce adapter are prototypes until they are connected to the production
  path and covered by integration tests.
- Local-model fine-tuning, additional platform adapters, and academic publication
  are possible future explorations. They have no delivery or submission schedule.

## Intentionally out of scope

- Access-control bypass, CAPTCHA bypass, anti-detection guarantees, or private-page harvesting.
- Arbitrary shell, Python, browser JavaScript, or model-installed plugins.
- Unreviewed automatic publication.
- Performance, compatibility, or platform-coverage claims without a reproducible benchmark.

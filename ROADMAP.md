# Generic Crawler Roadmap

This is the canonical roadmap for the configurable crawler. The approval-first
research assistant is an optional orchestration layer built on the crawler and
local document tools.

The repository is currently an **Alpha**. The latest published release remains
`v2.0.1`; the current source tree is a `v2.1.0` candidate and must not be tagged
until the release gates below are complete.

## v2.1.0 crawler-first alpha gates

- **Core distribution:** [#3](https://github.com/Ulysses-G-Yang/approval-first-research-automation/issues/3) — build and smoke-test installable wheel and sdist artifacts, including the crawler engine.
- **Optional assistant integrity:** [#4](https://github.com/Ulysses-G-Yang/approval-first-research-automation/issues/4) — bind approvals to the exact execution manifest.
- **Optional assistant integrity:** [#5](https://github.com/Ulysses-G-Yang/approval-first-research-automation/issues/5) — prevent concurrent execution, recover interrupted tasks, and preserve immutable artifacts.
- **Core plus assistant network boundary:** [#6](https://github.com/Ulysses-G-Yang/approval-first-research-automation/issues/6) — make approved HTTP and browser access explicit while documenting the separate trusted-config boundary of standalone `GenericSpider`.
- **Release:** [#9](https://github.com/Ulysses-G-Yang/approval-first-research-automation/issues/9) — add a gated release workflow after the foundation issues close.

Every gate requires tests that exercise the installed package or a controlled
local fixture. A green unit-test badge alone is not sufficient evidence for
release.

## Core crawler next

These are follow-up engineering investigations, not v2.1.0 promises:

- [#7](https://github.com/Ulysses-G-Yang/approval-first-research-automation/issues/7) — replace the parallel selector-repair implementations with one tested `GenericSpider` extraction pipeline.
- [#8](https://github.com/Ulysses-G-Yang/approval-first-research-automation/issues/8) — establish frozen page-evolution fixtures, failure cases, and measurable baselines.
- Define a stable installed crawler CLI only after the package surface and configuration contract are verified.
- Consider optional dependency groups for assistant providers and document conversion after the crawler distribution is stable.

## Experimental, not committed

- The five-layer self-healing engine, quality gate, repair memory, and generic
  e-commerce adapter remain prototypes until they are connected to
  `GenericSpider`, covered by integration tests, and measured against a
  reproducible benchmark.
- Additional platform adapters and local-model integration have no delivery
  schedule.
- Academic publication is outside the product roadmap. No novelty or model
  improvement claim will be made without reproducible experiments.

## Intentionally out of scope

- Access-control bypass, CAPTCHA bypass, anti-detection guarantees, or private-page harvesting.
- Claims that the crawler supports arbitrary websites or a fixed number of platforms without a reproducible benchmark.
- Arbitrary model-installed plugins in the optional assistant.
- Unreviewed automatic publication.

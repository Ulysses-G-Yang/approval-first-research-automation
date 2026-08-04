# JS-Adaptive Generic Crawler Roadmap

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
- **Assistant network enforcement plus standalone boundary documentation:** [#6](https://github.com/Ulysses-G-Yang/approval-first-research-automation/issues/6) — make approved HTTP and browser access explicit while documenting the separate trusted-config boundary of standalone `GenericSpider`.
- **Post-v2.1 network hardening:** [#14](https://github.com/Ulysses-G-Yang/approval-first-research-automation/issues/14) — connection pinning and aggregate browser-byte metering; explicitly non-blocking for v2.1.
- **Release:** [#9](https://github.com/Ulysses-G-Yang/approval-first-research-automation/issues/9) — add a gated release workflow after the foundation issues close.

Every gate requires tests that exercise the installed package or a controlled
local fixture. A green unit-test badge alone is not sufficient evidence for
release.

The delivery order is fixed so each boundary has independent review evidence:

1. #6 browser/network boundary integration.
2. #7 single extraction pipeline.
3. JS captures, Crawler CLI, and RepairEpisode Store.
4. #8 JS Evolution Benchmark.
5. #9 release workflow.
6. `release/v2.1.0` version and documentation commit.

The final commit changes `2.1.0.dev0` to `2.1.0`. A workflow-dispatch dry-run
must pass on that exact SHA before an annotated `v2.1.0` tag is created. The
Release contains only wheel, sdist, and checksum; after publication `main`
advances to `2.2.0.dev0`, and no existing tag is moved.

## v2.1 candidate implementation

- [#7](https://github.com/Ulysses-G-Yang/approval-first-research-automation/issues/7) uses one `GenericSpider` extraction pipeline and makes the old self-healing engine a compatibility facade.
- Optional embedded/network JSON captures preserve old YAML and source paths.
- RepairEpisode v1 uses an explicitly enabled local SQLite index plus SHA-256 CAS.
- The installed `crawler` CLI runs configs, benchmarks, and episode inspection/export.
- [#8](https://github.com/Ulysses-G-Yang/approval-first-research-automation/issues/8) is a frozen, offline, deterministic JS evolution baseline.
- [#9](https://github.com/Ulysses-G-Yang/approval-first-research-automation/issues/9) builds artifacts once and gates an immutable GitHub Release without PyPI publication.

These remain release-candidate statements until their pull requests and CI
evidence are merged. v2.1 remains Alpha.

## v2.2: controlled retrieval and plan repair

- Retrieve only approved similar episodes using local SQLite indexes, JSON
  schema fingerprints, DOM structure, failure type, and field semantics.
- Keep Analyzer, Fixer, and Reviewer prompts separate and versioned.
- Accept structured proposals only; JSON Patch paths stay allowlisted.
- Replay current and historical fixtures, require hard metrics and a human
  decision, and write a new configuration version instead of overwriting.

## v3: isolated Adapter PR and training experiments

- Generated changes are limited to one Adapter, its manifest, owned fixtures,
  expected output, and safety statement; core changes remain human-authored.
- A human may use `gh` to open a PR only after local gates; never push generated
  code directly to `main`.
- Training export includes approved positive episodes, rejected proposals,
  provenance, authorization category, and redaction records.
- Fine-tuning experiments require at least 500 approved episodes and 20
  independent page-evolution families. Splits are made by target family so no
  page version leaks across train, validation, and test.
- Export and evaluation interfaces remain provider-neutral and compare a
  deterministic baseline, general-model + retrieval, and fine-tuned candidates.
  A fine-tuned model may be labelled Experimental only when an independent
  holdout improves over general-model + retrieval without increasing invalid
  repair acceptance; otherwise retrieval remains the supported approach.
- The Assistant consumes normalized crawler records and Episode artifacts; it
  does not control the crawler core execution path.

## Bounded candidate capabilities

- The shared extraction pipeline, QualityGate, opt-in approved repair memory,
  and RepairEpisode evidence are connected to `GenericSpider` and covered by
  local integration tests and the JS evolution benchmark.
- The generic e-commerce Adapter remains **Limited**: it is explicitly selected
  and verified only with an owned synthetic fixture.
- LLM selector candidates remain **Experimental**, disabled by default, and
  cannot approve or persist themselves.
- Additional platform adapters, provider integrations, and model-quality claims
  have no delivery schedule.
- Academic publication is outside the product roadmap. No novelty or model
  improvement claim will be made without reproducible experiments.

## Intentionally out of scope

- Access-control bypass, CAPTCHA bypass, anti-detection guarantees, or private-page harvesting.
- Claims that the crawler supports arbitrary websites or a fixed number of platforms without a reproducible benchmark.
- Arbitrary model-installed plugins in the optional assistant.
- Unreviewed automatic publication.
- Model-authored overwrites of Python source or existing crawler configuration.
- Treating an LLM or reviewer opinion as proof of correctness.
- Persistent services, external databases, dashboards, or PyPI publication in v2.1.
- Saving credentials, private sessions, or unauthorized page data.

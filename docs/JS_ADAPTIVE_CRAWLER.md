# JS-Adaptive Generic Crawler

The v2.1 crawler keeps the existing `GenericSpider(config, network_policy=None)`
and old YAML/output contracts while adding bounded JavaScript-data capture and
an approval-first repair evidence layer. The crawler, store, replay benchmark,
and default Ollama repair option run locally and require no cloud service.
Gemini or Qwen are explicit optional configurations: enabling either sends a
truncated authorized page context to that provider and is not required by the
supported local path.

## Supported extraction order

Every selector-derived field follows one implementation and one QualityGate:

```text
configured selector
→ fallback_selectors
→ approved historical repair
→ Scrapling adaptive candidate
→ optional LLM selector candidate
→ empty
```

The LLM path is disabled by default and runs only after deterministic paths
fail or produce a validated but low-confidence adaptive result. A returned selector is re-extracted and validated. It may satisfy that
explicitly enabled run, but it is never written to configuration or promoted
to history automatically. `SelfHealingEngine` is a compatibility facade over
the same pipeline.

## JavaScript data captures

`captures` is optional, so old YAML remains valid.

- `embedded_json` reads JSON text through a CSS `selector`.
- `network_json` passively records the first URL-glob match that is a GET,
  2xx, XHR/fetch response with a JSON content type.
- `required` decides whether a missing/invalid/oversized capture fails the page.
- `max_bytes` is mandatory in the contract and bounded by the implementation.
- Existing dotted `source` paths read capture values, including list indexes.
- Existing `request.wait_until`, `wait_for_selector`, and `timeout_ms` control
  delayed hydration.

Capture plans do not add requests. Direct crawler YAML remains trusted,
code-like input and may still contain legacy actions; model repair patches may
not change actions, start URLs, browser settings, credentials, profiles, proxy
settings, or model endpoints.

## RepairEpisode v1

Passing `--experience-store PATH` opts into a local SQLite index and adjacent
SHA-256 content-addressed object directory. Omit it and no store or user-home
file is created.

Authorized non-synthetic pages remain structure-only unless the operator also
passes `--retain-full-episode-content` and labels the source
`authorization_category: authorized`. That explicit opt-in retains only
redacted text/JSON content; public/unknown sources cannot opt in, and secret or
session patterns are still removed.

An episode aggregates the target/page version, extraction-plan and page-feature
hashes, affected fields, failed stage, QualityGate result, candidate proposals,
model/prompt provenance when applicable, replay validation, reviewer notes,
human decisions, artifact hashes, and the source authorization category.

`synthetic_local` episodes retain full redacted fixture content by default.
An `authorized` source may retain redacted full content only with the explicit
CLI opt-in described above; every other case stores structural summaries. The
store removes credential/session keys and common Cookie, Authorization,
API-key, browser-profile, localStorage, and token patterns. Legacy
RepairPersistence JSONL can be imported only as historical candidates;
successful records are not automatically approved.

The allowed plan-patch roots are `fields`, `captures`, `validation`, and bounded
wait controls nested under `request`. A human decision is required before a future version can promote
a candidate. v2.1 offers inspection/export only; it does not modify source code
or overwrite configuration.

## Compatibility notes

`SelfHealingEngine` remains available as a facade over this same pipeline. Its
`llm_model` argument continues to select a local Ollama model. Repair memory is
now intentionally disabled by default; callers that want to reuse previously
approved JSONL repairs must pass an explicit `repair_db_path`. This prevents a
normal run from creating files in the user profile.

Legacy `browser.stealth` remains parse-compatible but is a no-op; v2.1 provides
no detection-evasion behavior. The removed live Douban verification and browser
publishing paths exist only on historical tags and are not part of the current
supported or authorized workflow.

## Benchmark and limits

`crawler benchmark --json --check-baseline` replays the checked-in synthetic
corpus twice without a browser, external network, or real model. It gates exact
normal extraction, all declared recoverable cases, invalid-candidate rejection,
correct irrecoverable failure, expected pipeline stage, and deterministic
artifact identity. Timing is reported but is not a cross-machine gate.

`max_bytes` is an acceptance limit and response bodies are time-bounded. For a
chunked response with no trustworthy length header, Playwright must finish the
body before the post-read size check; aggregate transport-byte metering and
connection pinning remain follow-up hardening in [#14](https://github.com/Ulysses-G-Yang/approval-first-research-automation/issues/14).

This Alpha demonstrates a local pipeline and evidence foundation. It does not
claim arbitrary-site compatibility, a strong browser network sandbox, CAPTCHA
or access-control bypass, risk-control evasion, or model correctness.

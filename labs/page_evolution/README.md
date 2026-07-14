# Page Evolution Lab

This is a local compatibility lab for explaining how extraction can change as a synthetic page evolves. It is deliberately separate from real websites, browser profiles, and target-specific scripts.

## What it demonstrates

| Fixture | Change | Observed path |
| --- | --- | --- |
| `catalog-v1.html` | Original markup. | Configured selector succeeds. |
| `catalog-v2.html` | Class names drift to semantic data attributes. | The engine's adaptive branch is exercised with a deterministic local adapter. |
| `catalog-v3.html` | Markup and an inline JSON state schema change. | A mocked, page-local selector candidate is reviewed and retried once. |

The adaptive adapter and selector candidate are deterministic teaching doubles. They make the control path repeatable; they are not a performance claim about Scrapling or an LLM.

## Run it

From the repository root:

```bash
python -m labs.page_evolution.run_lab
python -m labs.page_evolution.run_lab --json
```

The runner only reads the files under `labs/page_evolution/fixtures/`. It does not start Playwright, open a URL, make a provider request, evaluate page JavaScript, or write an output file.

## Why this is the education path

The historical `v1.0-educational` material is a site-specific extraction example. This lab instead makes the general engineering question visible: how should a workflow react when HTML structure or serialized page state changes?

Use it to add regression fixtures, verify failure modes, and discuss configuration migration. Do not use it as a basis for bypassing access controls or targeting a third-party service.

# Product Demo Assets

`docs/assets/product-preview.png` is rendered locally from `scripts/render_product_preview.py`. It uses only inline HTML and CSS, so it makes no network request and contains no synthetic performance claim.

Regenerate the static preview:

```bash
python scripts/render_product_preview.py
```

`docs/assets/workflow-overview.gif` is a three-frame visual overview generated from the same local HTML. It illustrates the product sequence only; it is not a recording of a live website, provider call, or publication.

```powershell
powershell -ExecutionPolicy Bypass -File scripts\render_workflow_overview.ps1
```

For a release GIF, record a real terminal session with the local `examples/research-report` workflow:

1. Run `agent doctor` and `agent list-workflows`.
2. Create the example task with `--workspace-root .demo-tasks`.
3. Show the printed plan before approving `step-01`.
4. Approve and resume each step.
5. Open the resulting `artifacts/report.md` and `artifacts/sources.jsonl`.

Do not record a browser login, a real API key, a personal document, or a platform publication. The GIF should show a real, local, reproducible workflow rather than a simulated external success.

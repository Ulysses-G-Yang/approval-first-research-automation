# Product Release Checklist

## Before a pull request

- [ ] `agent doctor` passes in the verified Python 3.12 environment.
- [ ] `agent list-workflows` shows every bundled workflow.
- [ ] `python -m unittest discover -s tests -v` passes without third-party network calls.
- [ ] `python -m compileall -q extract_prices.py agent.py core research_assistant scripts labs` passes.
- [ ] `python -m labs.page_evolution.run_lab --json` returns three non-empty local results.
- [ ] `python scripts/render_product_preview.py` recreates the social preview.
- [ ] `powershell -ExecutionPolicy Bypass -File scripts\render_workflow_overview.ps1` recreates a three-frame overview GIF.
- [ ] `git diff --check` passes and no secret-like or local workspace file is staged.

## Before merge and `v2.1.0`

- [ ] The target default branch contains the complete product surface.
- [ ] The repository About text and Topics match `docs/LAUNCH_KIT.md`.
- [ ] GitHub social preview points to `docs/assets/product-preview.png`.
- [ ] The release notes distinguish the historical `v1.0-educational` tag from the active product line.
- [ ] `v2.0.0` and `v2.0.1` remain untouched.
- [ ] The release tag and GitHub Release point to the merged commit.

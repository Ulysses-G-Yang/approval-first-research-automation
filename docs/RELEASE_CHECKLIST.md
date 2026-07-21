# Product Release Checklist

## Before a pull request

- [ ] `agent doctor` passes in the verified Python 3.12 environment.
- [ ] `agent --version` reports the candidate version from installed package metadata.
- [ ] `agent list-workflows` shows every bundled workflow.
- [ ] `python -m unittest discover -s tests -v` passes without third-party network calls.
- [ ] `python -m compileall -q extract_prices.py agent.py core adapters research_assistant workflows scripts labs` passes.
- [ ] `python -m build` creates exactly one wheel and one source distribution.
- [ ] `python -m twine check dist/*` passes.
- [ ] A clean environment can install the wheel and `python -m pip check` passes.
- [ ] From outside the checkout, `scripts/package_smoke.py` imports `core`, `adapters`, and bundled workflows, then completes the offline file-report workflow through the installed `agent` entry point.
- [ ] `python -m labs.page_evolution.run_lab --json` returns three non-empty local results.
- [ ] `python scripts/render_product_preview.py` recreates the social preview.
- [ ] `powershell -ExecutionPolicy Bypass -File scripts\render_workflow_overview.ps1` recreates a three-frame overview GIF.
- [ ] `git diff --check` passes and no secret-like or local workspace file is staged.

## Before merge and `v2.1.0`

- [ ] Linux and Windows installed-package CI jobs pass from the built wheel.
- [ ] The candidate version remains `2.1.0.dev0`; the release commit changes it to `2.1.0` before creating the tag.
- [ ] `agent --version`, `research_assistant.__version__`, wheel metadata, and the `v2.1.0` tag all agree on `2.1.0`.
- [ ] The wheel and source distribution attached to the release are built from the tagged commit and pass the same package smoke test.
- [ ] The target default branch contains the complete product surface.
- [ ] The repository About text and Topics match `docs/LAUNCH_KIT.md`.
- [ ] GitHub social preview points to `docs/assets/product-preview.png`.
- [ ] The release notes distinguish the historical `v1.0-educational` tag from the active product line.
- [ ] `v2.0.0` and `v2.0.1` remain untouched.
- [ ] The release tag and GitHub Release point to the merged commit.

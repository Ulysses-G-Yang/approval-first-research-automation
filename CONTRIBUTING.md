# Contributing

Thanks for improving the research automation tool. Please keep contributions focused, reproducible and safe to run.

1. Create a branch from the maintained default branch.
2. Start with an explicit user result: a report, dataset, Markdown bundle, draft package, workflow, or local lab fixture.
3. Keep site-specific logic in a configuration file whenever possible. Do not add logic intended to evade access controls or detection.
4. Do not commit cookies, browser profiles, API keys, target-page snapshots containing private data, task workspaces, or crawler output.
5. Add an offline test and a self-authored local example for every new workflow or parser path.
6. Run `python -m unittest discover -s tests -v`, `python -m compileall -q extract_prices.py agent.py core research_assistant scripts labs`, and `git diff --check` before opening a pull request.
7. Explain expected output, external service requirements, safety boundary, and any new approval step in the pull request.

This repository is for lawful, permissioned collection and engineering research. Contributors must respect site terms, robots policies where applicable, rate limits and relevant law. The [Page Evolution Lab](labs/page_evolution/README.md) is the preferred place to demonstrate selector or page-state drift without targeting a real service.

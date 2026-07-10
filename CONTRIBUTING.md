# Contributing

Thanks for improving the framework. Please keep contributions focused, reproducible and safe to run.

1. Create a branch from `feat/general-crawler`.
2. Keep site-specific logic in a configuration file whenever possible.
3. Do not commit cookies, browser profiles, API keys, target-page snapshots containing private data, or crawler output.
4. Run `python -m unittest discover -s tests -v` and the syntax check from the CI workflow before opening a pull request.
5. Explain configuration changes, expected output and any external service requirements in the pull request.

This repository is for lawful, permissioned collection and engineering research. Contributors must respect site terms, robots policies where applicable, rate limits and relevant law.

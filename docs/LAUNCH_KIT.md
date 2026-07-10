# v2.1.0 Launch Kit

## One-line description

**Approval-first AI research automation for public web pages and local files. Turn sources into traceable Markdown, datasets, and review-ready draft packages.**

Chinese counterpart:

**可复核的 AI 研究与内容自动化：把公开网页与本地文件变成带来源的 Markdown、数据集与待审核草稿包。**

## GitHub repository surface

Apply these repository-wide settings only after the product branch is reviewed into the default branch.

### About

```text
Approval-first AI research automation for public web pages and local files. Turn sources into traceable Markdown, datasets, and review-ready draft packages.
```

### Topics

```text
ai-agent
research-automation
web-to-markdown
document-processing
workflow-automation
web-scraping
playwright
scrapling
llm
python
```

### Social preview

Upload `docs/assets/product-preview.png` as the repository social preview after the default branch contains it. The asset explains a real product flow and makes no reliability or performance claim.

## Release narrative

Title:

```text
v2.1.0 - Traceable AI research automation
```

Highlights:

- Public pages and local files become task-local, source-backed Markdown artifacts.
- Every tool action is visible and requires approval.
- OpenAI-compatible, Gemini, and Qwen planning adapters use a credential reference instead of a YAML API key.
- DOCX and text PDFs retain local image assets in Markdown bundles.
- Offline platform packages are explicitly not uploads or publications.
- The Page Evolution Lab demonstrates bounded recovery paths without a real target site.

## Launch post hooks

Use one concrete, reproducible example per post.

1. **Why this agent asks before it acts**: show an approved local CSV-to-report task and its evidence folder.
2. **Word/PDF to a Markdown bundle without losing local images**: show the document workflow and validation artifact.
3. **What happens when a page changes**: show the local Page Evolution Lab, stressing that it is a compatibility lab rather than an anti-bot tool.
4. **From research to a review-ready draft package**: show `published: false` and explain why formal publishing remains separate.

## Release gate

Do not tag or announce `v2.1.0` until all of the following are true:

- The product branch has passed its offline test suite.
- A clean Python 3.12 environment can run the examples.
- README statements match `PRODUCT_SCOPE.md`.
- The PR is reviewed and merged into the default branch.
- The About text, Topics, social preview, and release notes point to the same shipped commit.

## First-week feedback loop

- Invite users to run one local example and report the unclear step, not merely to star the project.
- Treat workflow requests and synthetic Page Evolution fixtures as contribution paths.
- Track clones, visitors, issues, example failures, and repeat contributors alongside stars.
- Publish fixes and release notes only for verified behavior; do not use inflated metrics or automated star campaigns.

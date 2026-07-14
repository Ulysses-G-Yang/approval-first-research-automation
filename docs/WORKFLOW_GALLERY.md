# Workflow Gallery

The fastest way to understand the project is to start with a result, not an implementation detail. Every workflow below is approval-gated and writes to one task workspace.

## 1. Local data to a report

Use `file_report` for CSV, JSON, TXT, or Markdown you already have.

```bash
agent run "Summarize the example market notes" \
  --workflow file_report \
  --input examples/research-report/market-notes.csv
```

The plan reads only the supplied file, normalizes records, and writes a report plus `sources.jsonl` after each step is approved.

## 2. A document with images to Markdown

Use `document_to_markdown` when you need a portable Markdown bundle.

```bash
agent run "Convert my article" \
  --workflow document_to_markdown \
  --input examples/document-to-markdown/source/article.md
```

DOCX images, PDF embedded images, and safe relative Markdown images become local task assets. The validation step checks that generated local references remain resolvable.

## 3. An article to an offline platform package

Use `content_save_draft` after you have reviewed an article. The resulting package is local only.

```bash
agent run "Prepare a Juejin draft package" \
  --workflow content_save_draft \
  --platform juejin \
  --input examples/content-draft/source/article.md
```

The manifest explicitly records `network_access: false` and `published: false`. A future publisher adapter must be reviewed and separately approved.

## 4. Explicit public sources to a research brief

Use `research_report` or `web_to_markdown` with public URLs that you intentionally provide.

```bash
agent run "Compare these public product pages" \
  --workflow research_report \
  --url https://example.com/a \
  --url https://example.com/b
```

The plan shows the host of every network action before it can run. It never expands a URL list into browsing without a visible, approved step.

## 5. Configuration-driven browser extraction

Developers can reuse a reviewable crawler YAML through `crawler_report`.

```bash
agent run "Extract the approved public listing" \
  --workflow crawler_report \
  --input configs/site.yaml
```

The assistant rejects YAML actions and plaintext LLM keys in this workflow. The original standalone crawler remains available for authorized engineering experiments, but the assistant does not grant it an unrestricted browser surface.

## 6. Page Evolution Lab

The [local lab](../labs/page_evolution/README.md) simulates selector drift and versioned page-state changes without visiting a real website. It is the recommended way to demonstrate the extraction recovery path.

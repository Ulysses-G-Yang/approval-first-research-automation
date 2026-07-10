# Document to Markdown Example

The input is a self-authored Markdown file with a local SVG asset. The same workflow also accepts DOCX, TXT, and text PDFs.

```bash
agent run "Convert the local document example" \
  --workflow document_to_markdown \
  --input examples/document-to-markdown/source/article.md \
  --workspace-root .demo-tasks
```

After the three approved steps, compare the task's `artifacts/documents/*/article.md` to [expected/article.md](expected/article.md). The generated file points to a copied task-local asset named `assets/image-001.svg`.

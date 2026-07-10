# Offline Draft Package Example

Use this workflow to prepare a local handoff package for a platform. It stops before any platform action.

```bash
agent run "Prepare the local draft example" \
  --workflow content_save_draft \
  --platform juejin \
  --input examples/content-draft/source/article.md \
  --workspace-root .demo-tasks
```

The final manifest matches the important fields in [expected/draft-manifest.json](expected/draft-manifest.json). It is evidence that no network access or publication occurred, not a publication receipt.

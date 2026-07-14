# Research Report Example

This example has no external URL and no provider requirement. It demonstrates that a report can still be useful and traceable when the source is a file you explicitly supplied.

```bash
agent run "Summarize the example market notes" \
  --workflow file_report \
  --input examples/research-report/market-notes.csv \
  --workspace-root .demo-tasks
```

Approve the steps in order. The final task workspace contains `artifacts/report.md`, `artifacts/sources.jsonl`, the normalized dataset, its plan, and approvals.

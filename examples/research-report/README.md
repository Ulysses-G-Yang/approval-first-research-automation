# Research Report Example

This example has no external URL and no provider requirement. It demonstrates that a report can still be useful and traceable when the source is a file you explicitly supplied.

```bash
agent run "Summarize the example market notes" \
  --workflow file_report \
  --input examples/research-report/market-notes.csv \
  --workspace-root .demo-tasks
```

Approve the steps in order. The final task workspace contains versioned `report.md`, `sources.jsonl`, and normalized-dataset artifacts under `artifacts/versions/<attempt-id>/`, plus its plan and approvals. Use `agent status <TASK_ID>` for their exact paths.

# ADOS Project Configuration Template

```json
{
  "project": {
    "id": "example-project",
    "primary_repository_path": "<absolute-or-resolved-local-path>",
    "default_branch": "main",
    "allowed_primary_local_paths": []
  },
  "execution_policy": {
    "schema_version": "1",
    "publication": {
      "merge_strategy": "<merge|squash|rebase>"
    },
    "review": {
      "reviewer": "<reviewer-command>",
      "max_rounds": 5
    },
    "cleanup": {
      "autonomous": true
    },
    "guardian": {
      "stop_on_uncertain": true
    },
    "validation": {
      "commands": [
        "<focused/full project command>"
      ]
    }
  }
}
```

Project configuration supplies environment-specific facts. It must not weaken core truthfulness, worktree-first, independent-review, or Exact HEAD rules without an explicit documented constitutional amendment.

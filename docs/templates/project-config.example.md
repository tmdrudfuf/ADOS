# ADOS Project Configuration Template

```yaml
project:
  id: example-project
  primary_repository_path: <absolute-or-resolved-local-path>
  default_branch: main
  allowed_primary_untracked_paths: []

roles:
  implementer:
    adapter: codex-cli
  reviewer:
    adapter: claude-cli
    max_review_rounds: 5

validation:
  commands:
    - <focused/full project command>

publication:
  push_requires_human: true
  ready_requires_human: true
  merge_requires_human: true
  deploy_requires_human: true

cleanup:
  archive_review_artifacts: true
  branch_deletion_requires_human: true
  worktree_deletion_requires_human: true
  remote_branch_deletion_requires_human: true
```

Project configuration supplies environment-specific facts. It must not weaken core truthfulness, worktree-first, independent-review, or Exact HEAD rules without an explicit documented constitutional amendment.

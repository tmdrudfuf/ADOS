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
  conditional_autonomous_merge:
    enabled: true
    standard_merge_strategy: <merge|squash|rebase>
  push_requires_human_unless_autonomous_gate_passes: true
  ready_requires_human_unless_autonomous_gate_passes: true
  merge_requires_human_unless_autonomous_gate_passes: true
  deploy_requires_human: true

cleanup:
  archive_review_artifacts: true
  proven_safe_post_merge_cleanup_allowed: true
  branch_deletion_requires_human_unless_proven_safe: true
  worktree_deletion_requires_human_unless_proven_safe: true
  remote_branch_deletion_requires_human_unless_proven_safe: true
```

Project configuration supplies environment-specific facts. It must not weaken core truthfulness, worktree-first, independent-review, or Exact HEAD rules without an explicit documented constitutional amendment.

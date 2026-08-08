# AIverse Integration Example

This file demonstrates how one project can configure ADOS without changing ADOS core rules.

```yaml
project:
  id: aiverse
  default_branch: main
  allowed_primary_untracked_paths:
    - .claude/

roles:
  implementer:
    adapter: codex-cli
  reviewer:
    adapter: claude-cli
    max_review_rounds: 5

validation:
  commands:
    - npm test
    - npx tsc --noEmit
    - npm run build
    - git diff --check
    - git diff --cached --check

publication:
  conditional_autonomous_merge:
    enabled: true
    standard_merge_strategy: <project-configured-strategy>
  push_requires_human_unless_autonomous_gate_passes: true
  ready_requires_human_unless_autonomous_gate_passes: true
  merge_requires_human_unless_autonomous_gate_passes: true

cleanup:
  archive_review_artifacts: true
  proven_safe_post_merge_cleanup_allowed: true
  worktree_deletion_requires_human_unless_proven_safe: true
  branch_deletion_requires_human_unless_proven_safe: true
  remote_branch_deletion_requires_human_unless_proven_safe: true
```

Local absolute repository/worktree paths should be supplied by local project configuration rather than committed into ADOS core documentation.

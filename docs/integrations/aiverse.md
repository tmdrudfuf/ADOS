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
  push_requires_human: true
  ready_requires_human: true
  merge_requires_human: true

cleanup:
  archive_review_artifacts: true
  worktree_deletion_requires_human: true
  branch_deletion_requires_human: true
  remote_branch_deletion_requires_human: true
```

Local absolute repository/worktree paths should be supplied by local project configuration rather than committed into ADOS core documentation.

# Data Model: Worktree Lifecycle Engine

## WorktreeRequest

- primaryRepositoryPath
- worktreePath
- branch
- baseRef
- expectedPrimaryBranch
- expectedPrimaryHead
- allowedPrimaryLocalPaths[]

## WorktreeLifecycleResult

- operation
- status: PASS | BLOCK
- violations[]
- evidence[]

## WorktreeViolation

- code
- message
- evidence

## WorktreeRecord

- path
- branch
- head

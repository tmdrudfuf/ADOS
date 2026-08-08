# Research: Worktree Lifecycle Engine

## Git worktree operations

Creation uses:

- `git worktree add -b <branch> <worktree-path> <base-ref>`

Removal uses:

- `git worktree remove <worktree-path>`

Both commands are scoped to explicit paths and branches. No wildcard, reset, clean, stash, push, or remote mutation is part of Spec003.

## Cleanup authority

Worktree removal is controlled by `execution_policy.cleanup.autonomous`. If false, removal returns `CLEANUP_AUTONOMY_DISABLED`.

## Registration checks

`git worktree list --porcelain` provides registered worktree paths and branches without mutation. The engine verifies the requested path appears in that list before removal and after creation.

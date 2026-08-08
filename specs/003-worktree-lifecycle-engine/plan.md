# Implementation Plan: Worktree Lifecycle Engine

## Summary

Spec003 adds `WorktreeLifecycleEngine`, a narrow lifecycle service that uses Execution Policy and Primary Repository Guardian before creating or removing feature worktrees.

## Architecture

```text
CLI
  -> Execution Policy
  -> Worktree Lifecycle Engine
  -> Primary Repository Guardian
  -> Worktree Provider
  -> Git worktree commands
```

## Design Decisions

### D1 - Explicit request model

The engine accepts explicit primary path, worktree path, branch, and base ref. It does not infer branch names or filesystem locations.

### D2 - Guardian-gated creation

Create runs the Primary Repository Guardian first. A blocked primary repository prevents worktree creation.

### D3 - Narrow removal only

Remove requires cleanup autonomy in policy and removes only the registered worktree path. Branch and remote cleanup remain publication-engine responsibilities.

### D4 - Deterministic evidence

Lifecycle results use `PASS` or `BLOCK` and stable violation codes for orchestration.

## Validation

Focused validation:

```text
python -m unittest tests.test_worktree_lifecycle
```

Full validation:

```text
python -m unittest discover
python -m ados policy validate --policy tests/fixtures/execution-policy.valid.json
python -m ados guardian primary --policy tests/fixtures/execution-policy.valid.json --repo . --expected-branch codex/003-worktree-lifecycle-engine
git diff --check
```

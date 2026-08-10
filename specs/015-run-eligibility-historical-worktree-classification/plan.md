# Plan

## Architecture

`ados status` and `ados run` share a read-only worktree classification boundary.

CLI
-> Status / Run application services
-> Worktree classification
-> Git worktree provider + read-only Git provider

## Classification

- `ACTIVE`: current unfinished durable run or unmerged Spec worktree.
- `MERGED_HISTORICAL`: clean worktree with HEAD reachable from current main, or clean Spec worktree whose Spec is not newer than the latest merged Spec evidence.
- `PRESERVED`: clean non-Spec worktree with unmerged history that should not be discarded.
- `UNKNOWN`: missing, dirty, unreadable, or otherwise unprovable state.

## Validation

Focused:

```powershell
python -m unittest tests.test_cli_status tests.test_cli_run
```

Full:

```powershell
python -m unittest discover
python -m ados validation run --policy tests/fixtures/execution-policy.valid.json --repo .
git diff --check
git diff --cached --check
```

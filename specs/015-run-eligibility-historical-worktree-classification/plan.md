# Plan

## Architecture

`ados status` and `ados run` share a read-only worktree classification boundary.

CLI
-> Status / Run application services
-> Worktree classification
-> Git worktree provider + read-only Git provider

## Classification

- `ACTIVE`: current unfinished durable run or unmerged Spec worktree.
- `MERGED_HISTORICAL`: clean non-Spec worktree with HEAD reachable from current main, clean Spec worktree whose HEAD is reachable from a merged archive commit for that exact Spec, or clean worktree whose HEAD is reachable from a merged pull request head whose merge commit is reachable from current main.
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

# Implementation Plan: ADOS Run Command Foundation

## Architecture

CLI
-> Run application service
-> Doctor / Status / Guardian
-> Spec resolver
-> Worktree Lifecycle Engine
-> Workflow run record
-> Implementation handoff

The run service is intentionally a startup coordinator only. It mutates only by creating a dedicated worktree and writing the run record inside that worktree after verification.

## Validation

Focused:

```powershell
python -m unittest tests.test_cli_run tests.test_cli_status tests.test_worktree_lifecycle
```

Full:

```powershell
python -m unittest discover
python -m ados validation run --policy tests/fixtures/execution-policy.valid.json --repo .
git diff --check
git diff --cached --check
```

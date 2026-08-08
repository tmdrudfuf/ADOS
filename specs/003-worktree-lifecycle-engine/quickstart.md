# Quickstart: Worktree Lifecycle Engine

Validate policy:

```powershell
python -m ados policy validate --policy tests/fixtures/execution-policy.valid.json
```

Verify a worktree:

```powershell
python -m ados worktree verify --policy tests/fixtures/execution-policy.valid.json --primary-repo . --worktree-path ..\.worktrees\example --branch feature/example
```

Create a worktree:

```powershell
python -m ados worktree create --policy tests/fixtures/execution-policy.valid.json --primary-repo . --worktree-path ..\.worktrees\example --branch feature/example --base-ref main --expected-primary-branch main
```

Remove a worktree:

```powershell
python -m ados worktree remove --policy tests/fixtures/execution-policy.valid.json --primary-repo . --worktree-path ..\.worktrees\example --branch feature/example
```

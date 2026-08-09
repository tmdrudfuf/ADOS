# Quickstart

```powershell
python -m ados run --project C:\path\to\project --feature "Add focused feature"
python -m ados run --project C:\path\to\project --feature "Plan only" --dry-run
```

Successful real runs create a worktree, write a run record, invoke the configured implementer, then stop at `READY_FOR_VALIDATION`.

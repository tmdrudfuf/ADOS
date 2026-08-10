# Quickstart

Run status:

```powershell
python -m ados status --project C:\Users\tmdru\Desktop\Ky-Project\AIverse --config $env:TEMP\ados-aiverse-project-config.valid.json
```

Run a read-only startup plan:

```powershell
python -m ados run --project C:\Users\tmdru\Desktop\Ky-Project\AIverse --config $env:TEMP\ados-aiverse-project-config.valid.json --spec 084 --feature "Post-Validation Re-Review Decision & Continuation Foundation" --dry-run
```

Historical merged worktrees may appear as warnings. They must not create a worktree or start implementation in dry-run mode.

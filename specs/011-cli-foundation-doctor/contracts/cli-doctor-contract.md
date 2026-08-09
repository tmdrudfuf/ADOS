# Contract: `ados doctor`

## Commands

```powershell
python -m ados --help
python -m ados --version
python -m ados doctor --project <path> [--config <path>] [--json]
python -m ados doctor <path> [--config <path>] [--json]
```

## Exit Codes

- `0`: READY
- `1`: BLOCKED
- `2`: invalid usage, missing path/config, invalid config, or runtime error

## JSON Shape

```json
{
  "status": "READY",
  "checks": [
    {
      "id": "primary_repository_guardian",
      "status": "PASS",
      "summary": "Primary Repository Guardian",
      "blocking": true,
      "evidence": {},
      "violations": []
    }
  ]
}
```

JSON mode prints only this object to stdout.

## Mutation Boundary

Doctor must not write files, create worktrees or branches, execute validation commands, invoke reviewer workloads, push, create PRs, merge, deploy, or delete anything.

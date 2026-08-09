# Contract: `ados status`

## Commands

```powershell
python -m ados status --project <path> [--config <path>] [--json]
python -m ados status <path> [--config <path>] [--json]
```

## Exit Codes

- `0`: `IDLE`, `READY`, or `ACTIVE`
- `1`: `BLOCKED`
- `2`: `INVALID`

## Read-Only Contract

Status must not write files, create Specs, create/remove worktrees, execute validation, invoke Codex or Claude, push, create/update PRs, merge, restore/reset/clean/stash, or delete anything.

## JSON

JSON mode prints only the serialized StatusResult to stdout.

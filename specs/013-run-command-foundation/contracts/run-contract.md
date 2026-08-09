# Contract: `ados run`

## Command

```powershell
ados run --project <path> --feature "<description>" [--spec NNN] [--config <path>] [--json] [--dry-run]
```

## Exit Codes

- `0`: run started or dry-run plan is eligible
- `1`: run startup blocked
- `2`: invalid usage/config/runtime error

## JSON

JSON output serializes the full RunResult with stable keys and machine-readable violation evidence.

## Mutation Contract

Real run:

1. create worktree
2. verify worktree
3. write run record inside feature worktree

Dry-run:

No mutation.

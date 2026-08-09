# Contract: Implementer Runtime

## Input

An existing workflow run record with:

- `status = READY_FOR_IMPLEMENTATION`
- feature worktree path
- feature branch
- base SHA
- role adapters

## Output

Implementer runtime evidence JSON:

```json
{
  "status": "READY_FOR_VALIDATION",
  "runtime": {},
  "result": {},
  "violations": []
}
```

## Safety

The runtime must not use shell interpolation, must use the feature worktree as cwd, and must not start validation, review, or publication.

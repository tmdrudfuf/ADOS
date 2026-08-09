# Implementation Plan: ADOS Status Foundation

## Architecture

CLI app
-> Status command formatting/exit mapping
-> StatusService
-> DoctorService / ProjectConfig / Guardian / Git worktree provider / archive evidence reader

`ados status` remains a read-only diagnostic. It does not duplicate Doctor checks; it embeds Doctor status as one evidence source and adds workflow-state summarization.

## Implementation

- Add `ados.status` typed result model and service.
- Extend `ados.cli_app` with `status`.
- Read `.agent-workflow/runs/*/ados-review-evidence.json` when present.
- Report validation/review/exact-head/publication state only when archive evidence is SHA-bound.
- Add focused tests using temporary repositories and explicit config files.

## Validation

Focused:

```powershell
python -m unittest tests.test_cli_status
python -m ados status --project C:\Users\tmdru\Desktop\Ky-Project\AIverse --config <temp-config>
python -m ados status --project C:\Users\tmdru\Desktop\Ky-Project\AIverse --config <temp-config> --json
```

Full:

```powershell
python -m unittest discover
python -m ados validation run --policy tests/fixtures/execution-policy.valid.json --repo .
git diff --check
git diff --cached --check
```

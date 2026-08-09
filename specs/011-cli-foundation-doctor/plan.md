# Implementation Plan: ADOS CLI Foundation & Doctor

## Architecture

CLI entrypoint
-> command parser/dispatcher
-> Doctor application service
-> Project Configuration / Execution Policy / Primary Repository Guardian
-> read-only providers

The CLI layer owns argument parsing, formatting, and exit-code mapping. Existing engines keep their domain behavior.

## Implementation

- Add `ados.cli_app` for reusable parser construction and dispatch.
- Keep `ados.cli.main` as the public entrypoint.
- Add `ados.doctor` with typed result/check models and a read-only service.
- Add minimal project config discovery.
- Add deterministic human and JSON formatting.
- Add focused unit and CLI tests.

## Validation

Focused:

```powershell
python -m unittest tests.test_cli_doctor
python -m ados doctor --project C:\Users\tmdru\Desktop\Ky-Project\AIverse --config <temp-aiVerse-config>
python -m ados doctor --project C:\Users\tmdru\Desktop\Ky-Project\AIverse --config <temp-aiVerse-config> --json
```

Full:

```powershell
python -m unittest discover
python -m ados validation run --policy tests/fixtures/execution-policy.valid.json --repo .
git diff --check
git diff --cached --check
```

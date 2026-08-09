# Implementation Plan: Implementer Invocation / Run Orchestration

## Architecture

CLI `ados run`
-> RunService startup
-> durable run record
-> ImplementerRuntime
-> safe subprocess provider
-> runtime/result/evidence records
-> run state transition

The implementer runtime owns only invocation and immediate postconditions. Validation/review/publication remain separate engines.

## Validation

Focused:

```powershell
python -m unittest tests.test_implementer_runtime tests.test_cli_run
```

Full:

```powershell
python -m unittest discover
python -m ados validation run --policy tests/fixtures/execution-policy.valid.json --repo .
git diff --check
git diff --cached --check
```

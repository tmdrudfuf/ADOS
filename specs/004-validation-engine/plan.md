# Implementation Plan: Validation Engine

## Summary

Spec004 adds a policy-driven Validation Engine that runs explicit validation commands and returns exact HEAD-bound evidence.

## Architecture

```text
CLI
  -> Execution Policy
  -> Validation Engine
  -> Repository Provider
  -> Command Runner
```

## Design Decisions

### D1 - Policy-only commands

The engine does not accept ad hoc validation commands. It runs the command list already validated in Execution Policy.

### D2 - Exact HEAD binding

The engine records HEAD before and after command execution. If HEAD changes, validation blocks even when commands exit zero.

### D3 - Evidence over exceptions

Command failures are represented as `BLOCK` results with deterministic violation codes.

## Validation

Focused validation:

```text
python -m unittest tests.test_validation_engine
```

Full validation:

```text
python -m unittest discover
python -m ados validation run --policy tests/fixtures/execution-policy.valid.json --repo .
python -m ados guardian primary --policy tests/fixtures/execution-policy.valid.json --repo . --expected-branch codex/004-validation-engine
git diff --check
```

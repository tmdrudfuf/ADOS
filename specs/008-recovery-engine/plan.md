# Implementation Plan: Recovery Engine

## Summary

Spec008 adds a recovery classifier. It maps stop-condition evidence to a recommended next action and never mutates repository state.

## Architecture

```text
Workflow evidence
  -> Recovery Engine
  -> recovery decision
```

## Validation

Focused validation:

```text
python -m unittest tests.test_recovery_engine
```

Full validation:

```text
python -m unittest discover
python -m ados validation run --policy tests/fixtures/execution-policy.valid.json --repo .
python -m ados guardian primary --policy tests/fixtures/execution-policy.valid.json --repo . --expected-branch codex/008-recovery-engine-runtime
git diff --check
```

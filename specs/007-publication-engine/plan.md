# Implementation Plan: Publication Engine

## Summary

Spec007 adds a local publication gate evaluator. It consumes explicit evidence and the Execution Policy merge strategy, then returns `PERMITTED` or `HUMAN_INTERVENTION_REQUIRED`.

## Architecture

```text
CLI / Orchestrator
  -> Execution Policy
  -> Publication Engine
  -> explicit publication evidence
```

## Validation

Focused validation:

```text
python -m unittest tests.test_publication_engine
```

Full validation:

```text
python -m unittest discover
python -m ados validation run --policy tests/fixtures/execution-policy.valid.json --repo .
python -m ados guardian primary --policy tests/fixtures/execution-policy.valid.json --repo . --expected-branch codex/007-publication-engine-runtime
git diff --check
```

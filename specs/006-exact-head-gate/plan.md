# Implementation Plan: Exact HEAD Gate

## Summary

Spec006 adds an executable Exact HEAD Gate. It returns `MATCH` only when Approved Review SHA, Validated SHA, and current local HEAD are identical.

## Architecture

```text
CLI
  -> Exact HEAD Gate
  -> Repository Provider
  -> Read-only Git
```

## Validation

Focused validation:

```text
python -m unittest tests.test_exact_head_gate
```

Full validation:

```text
python -m unittest discover
python -m ados validation run --policy tests/fixtures/execution-policy.valid.json --repo .
python -m ados guardian primary --policy tests/fixtures/execution-policy.valid.json --repo . --expected-branch codex/006-exact-head-gate
git diff --check
```

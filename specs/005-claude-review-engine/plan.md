# Implementation Plan: Claude Review Engine

## Summary

Spec005 adds a review engine that invokes the reviewer configured in Execution Policy. The implementation is provider-neutral: it can run Claude CLI or any configured reviewer command.

## Architecture

```text
CLI
  -> Execution Policy
  -> Review Engine
  -> Configured reviewer command
```

## Design Decisions

### D1 - Provider-neutral command

The engine does not hardcode Claude. The default ADOS role remains Claude CLI, but the executable engine uses `execution_policy.review.reviewer`.

### D2 - Exact SHA binding

The review request must provide candidate and base SHAs. The result records the candidate as `reviewed_sha`.

### D3 - Deterministic decision parsing

Only top-level `Approved` or `Changes Requested` decisions are accepted. Anything else is `Unavailable`.

## Validation

Focused validation:

```text
python -m unittest tests.test_review_engine
```

Full validation:

```text
python -m unittest discover
python -m ados validation run --policy tests/fixtures/execution-policy.valid.json --repo .
python -m ados guardian primary --policy tests/fixtures/execution-policy.valid.json --repo . --expected-branch codex/005-claude-review-engine
git diff --check
```

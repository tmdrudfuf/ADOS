# Implementation Plan: Primary Repository Guardian + Execution Policy Foundation

## Summary

Spec002 adds the first executable ADOS runtime layer. It creates an immutable execution policy model, deterministic validation errors, a read-only Git provider, and a Primary Repository Guardian that reports repository safety without changing repository state.

## Architecture

```text
CLI
  -> Execution Policy
  -> Guardian Service
  -> Repository Provider
  -> Read-only Git
```

## Design Decisions

### D1 - No implicit defaults

Every required policy field must be present. Missing values return configuration errors instead of being inferred.

### D2 - Immutable runtime model

Validated policy objects are frozen dataclasses. Callers can serialize them but cannot mutate them in place.

### D3 - Provider-neutral policy

The execution policy records behavior, not provider identity. It does not name ADOS, GitHub, Codex, Claude, or local paths.

### D4 - Guardian detects only

The Primary Repository Guardian invokes only read-only Git commands and filesystem checks. It never repairs state.

### D5 - Deterministic violation codes

Every block reason has a stable code so later engines can consume guardian output without parsing prose.

## Implementation Scope

- `ados/execution_policy.py`
- `ados/repository_provider.py`
- `ados/git_provider.py`
- `ados/primary_repository_guardian.py`
- `ados/cli.py`
- `ados/__main__.py`
- focused `unittest` tests under `tests/`

## Validation

Focused validation:

```text
python -m unittest tests.test_execution_policy tests.test_primary_repository_guardian
```

Full validation:

```text
python -m unittest discover
python -m ados policy validate --policy tests/fixtures/execution-policy.valid.json
python -m ados guardian primary --policy tests/fixtures/execution-policy.valid.json --repo . --expected-branch codex/002-primary-repository-guardian
git diff --check
```

## Review

Claude CLI reviews the exact validated SHA against `main`. Approval is valid only when Approved Review SHA, Validated SHA, and current HEAD match.

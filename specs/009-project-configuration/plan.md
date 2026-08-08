# Implementation Plan: Project Configuration

## Summary

Spec009 adds a JSON project configuration model that carries project-specific facts and embeds `execution_policy`.

## Architecture

```text
CLI
  -> Project Configuration
  -> Execution Policy
```

## Validation

Focused validation:

```text
python -m unittest tests.test_project_config
```

Full validation:

```text
python -m unittest discover
python -m ados config validate --config tests/fixtures/project-config.valid.json
python -m ados validation run --policy tests/fixtures/execution-policy.valid.json --repo .
python -m ados guardian primary --policy tests/fixtures/execution-policy.valid.json --repo . --expected-branch codex/009-project-configuration-runtime
git diff --check
```

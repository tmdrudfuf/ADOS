# Implementation Plan: AIverse Integration

Spec010 keeps AIverse as an integration example. It does not add AIverse branches to core runtime code.

## Architecture

```text
AIverse project config
  -> Project Configuration
  -> Execution Policy
  -> Guardian Service
  -> Repository Provider
  -> Read-only Git
```

## Scope

- Add AIverse JSON configuration fixture.
- Align integration documentation and project template with executable Project Configuration.
- Add focused integration tests for loading and guardian input flow.
- Add Spec Kit artifacts.

## Validation

```powershell
python -m unittest tests.test_aiverse_integration
python -m ados config validate --config tests/fixtures/aiverse-project-config.valid.json
python -m unittest discover
python -m ados validation run --policy tests/fixtures/execution-policy.valid.json --repo .
git diff --check
```

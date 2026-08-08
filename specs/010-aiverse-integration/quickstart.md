# Quickstart: AIverse Integration

Validate the executable AIverse project configuration:

```powershell
python -m ados config validate --config tests/fixtures/aiverse-project-config.valid.json
```

Use the returned `project_config.execution_policy` as the policy input for validation, review, guardian, publication, and cleanup engines.

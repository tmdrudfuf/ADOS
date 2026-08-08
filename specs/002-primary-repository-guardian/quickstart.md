# Quickstart: Primary Repository Guardian + Execution Policy Foundation

1. Create a JSON execution policy with a top-level `execution_policy` object.
2. Validate the policy:

```powershell
python -m ados policy validate --policy tests/fixtures/execution-policy.valid.json
```

3. Audit a repository:

```powershell
python -m ados guardian primary --policy tests/fixtures/execution-policy.valid.json --repo . --expected-branch codex/002-primary-repository-guardian
```

4. Treat any `BLOCK` result as a workflow stop. The guardian does not repair state.

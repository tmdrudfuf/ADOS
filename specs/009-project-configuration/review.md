# Review: Project Configuration

## Local validation status

Focused validation passed:

- `python -m unittest tests.test_project_config`
- `python -m ados config validate --config tests/fixtures/project-config.valid.json`

Full validation passed:

- `python -m unittest discover`
- `python -m ados validation run --policy tests/fixtures/execution-policy.valid.json --repo .`
- `git diff --check`

Pre-commit guardian check blocked only on the intended uncommitted Spec009 changes. Run the guardian again after commit for the exact candidate HEAD.

## Review scope

Claude must review exact validated SHA for:

- no implicit project defaults;
- immutable/serializable config model;
- Execution Policy reuse;
- provider-neutral and project-neutral behavior.

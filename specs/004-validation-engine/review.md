# Review: Validation Engine

## Local validation status

Focused and full validation passed before candidate commit.

Claude independent review is pending.

## Review scope

Claude must review exact validated SHA for:

- commands sourced only from Execution Policy;
- exact HEAD binding;
- deterministic command failure and HEAD drift violations;
- CLI behavior and tests.

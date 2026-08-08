# Feature Specification: AIverse Integration

**Feature Branch**: `codex/010-aiverse-integration-runtime`
**Status**: Draft
**Purpose**: Provide an executable AIverse adoption example using the project-neutral Project Configuration and Execution Policy models.

## User Story 1 - Validate AIverse configuration (P1)

An orchestrator can validate an AIverse project configuration with the same runtime model used by any other project.

### Acceptance

1. AIverse configuration uses the `project` and `execution_policy` JSON shape.
2. Required project facts are explicit.
3. Execution Policy facts are validated by the reusable Execution Policy model.
4. No AIverse-specific default is embedded in ADOS runtime code.

## User Story 2 - Drive guardian from AIverse configuration (P1)

An orchestrator can pass AIverse project facts and Execution Policy into Primary Repository Guardian without translating undocumented names.

### Acceptance

1. The expected primary repository path comes from project configuration.
2. The expected branch comes from project configuration.
3. Allowed local paths come from project configuration.
4. Guardian behavior remains read-only.

## Functional Requirements

- FR-001: Provide a valid AIverse project configuration fixture.
- FR-002: Update AIverse documentation to use the executable JSON configuration shape.
- FR-003: Update the project configuration template to match the executable model names.
- FR-004: Test that AIverse configuration loads without implicit defaults.
- FR-005: Test that AIverse configuration can drive Primary Repository Guardian.

## Non-Goals

- Connecting to a real AIverse repository.
- Hardcoding AIverse paths, branches, commands, providers, or adapters in runtime code.
- Adding YAML parsing.
- Changing publication, review, validation, or guardian semantics.

## Success Criteria

- `python -m ados config validate --config tests/fixtures/aiverse-project-config.valid.json` passes.
- AIverse tests prove the fixture derives Execution Policy and Guardian inputs.
- Core runtime code remains project-neutral.

# Feature Specification: Project Configuration

**Feature Branch**: `codex/009-project-configuration-runtime`
**Status**: Draft
**Purpose**: Add validated project configuration that supplies project-specific facts and derives the provider-neutral Execution Policy.

## User Story 1 - Load project configuration (P1)

An orchestrator can load a JSON project configuration and receive validated project facts plus an immutable Execution Policy.

### Acceptance

1. Project id, primary repository path, default branch, and allowed local paths are explicit.
2. Execution policy is embedded and validated by the existing Execution Policy model.
3. Missing fields return deterministic configuration errors.
4. The model is serializable and immutable.
5. No project name, branch, path, or provider is hardcoded.

## Functional Requirements

- FR-001: Implement immutable project configuration model.
- FR-002: Validate required project fields without implicit defaults.
- FR-003: Reuse Execution Policy validation for embedded runtime policy.
- FR-004: Expose allowed primary local paths for guardian callers.
- FR-005: Add CLI command to validate project config.
- FR-006: Add focused tests.

## Non-Goals

- YAML parsing.
- Environment variable expansion.
- Writing config files.
- Running workflow phases.

# Feature Specification: Validation Engine

**Feature Branch**: `codex/004-validation-engine`
**Status**: Draft
**Purpose**: Execute validation commands from Execution Policy and bind results to an exact repository HEAD.

## User Story 1 - Run policy-defined validation (P1)

An orchestrator can run the commands listed in `execution_policy.validation.commands` and receive deterministic pass/block evidence.

### Acceptance

1. Commands come only from Execution Policy.
2. Commands run in an explicit repository path.
3. Each command records command text, exit code, stdout, and stderr.
4. Any nonzero command blocks validation.
5. The result records the repository HEAD before and after validation.
6. HEAD drift during validation blocks validation.

## Functional Requirements

- FR-001: Implement validation result and command result records.
- FR-002: Execute validation commands from immutable Execution Policy.
- FR-003: Bind validation evidence to exact HEAD.
- FR-004: Detect command failure.
- FR-005: Detect HEAD drift.
- FR-006: Add CLI command for validation run.
- FR-007: Add focused tests for pass, command failure, and HEAD drift.

## Non-Goals

- Choosing validation commands.
- Installing dependencies.
- Retrying failed commands.
- Repairing repository state.
- Publication or review automation.

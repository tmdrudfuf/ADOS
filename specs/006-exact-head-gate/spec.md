# Feature Specification: Exact HEAD Gate

**Feature Branch**: `codex/006-exact-head-gate`
**Status**: Draft
**Purpose**: Implement the executable gate that proves reviewer approval, validation, and current repository HEAD all refer to the same exact commit.

## User Story 1 - Verify exact candidate identity (P1)

An orchestrator can compare Approved Review SHA, Validated SHA, and Current HEAD and receive `MATCH` only when all three are identical.

### Acceptance

1. Approved Review SHA is an explicit input.
2. Validated SHA is an explicit input.
3. Current HEAD is read from the repository.
4. Any mismatch blocks with deterministic violation codes.
5. The gate never changes repository state.

## Functional Requirements

- FR-001: Implement exact head gate result and violation records.
- FR-002: Read current HEAD through the repository provider.
- FR-003: Detect approved-review mismatch.
- FR-004: Detect validation mismatch.
- FR-005: Add CLI command for exact gate verification.
- FR-006: Add focused tests for match and mismatch cases.

## Non-Goals

- Remote branch or PR SHA comparison.
- Publication.
- Review invocation.
- Validation command execution.

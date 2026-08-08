# Feature Specification: Recovery Engine

**Feature Branch**: `codex/008-recovery-engine-runtime`
**Status**: Draft
**Purpose**: Provide deterministic recovery classification and recommendations for unsafe workflow states without performing repairs.

## User Story 1 - Classify stop conditions (P1)

An orchestrator can submit evidence from guardians, validation, review, exact-head gate, and publication gate and receive a deterministic recovery decision.

### Acceptance

1. Unsafe primary repository evidence blocks.
2. Validation failure evidence blocks.
3. Claude Changes Requested evidence recommends fix loop.
4. Exact HEAD mismatch evidence recommends revalidation and review.
5. Publication gate failure evidence returns human intervention.
6. Unknown evidence returns human intervention.
7. The engine never repairs or mutates state.

## Functional Requirements

- FR-001: Implement recovery issue/result records.
- FR-002: Classify known stop condition codes deterministically.
- FR-003: Produce recommended next action without executing it.
- FR-004: Add focused tests for known and unknown stop states.

## Non-Goals

- Running repair commands.
- Reset, clean, stash, checkout, merge, push, or delete operations.
- Retrying validation or review.
- Publication.

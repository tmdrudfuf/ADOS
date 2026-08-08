# Feature Specification: Claude Review Engine

**Feature Branch**: `codex/005-claude-review-engine`
**Status**: Draft
**Purpose**: Add a provider-neutral independent review engine that invokes the reviewer configured by Execution Policy and binds the decision to an exact SHA.

## User Story 1 - Invoke configured independent reviewer (P1)

An orchestrator can request review for a candidate SHA and receive a deterministic review result.

### Acceptance

1. Reviewer command comes from `execution_policy.review.reviewer`.
2. Candidate SHA and base SHA are explicit inputs.
3. Review prompt includes exact candidate SHA and base SHA.
4. Reviewer output is parsed only as `Approved` or `Changes Requested`.
5. Unknown reviewer output blocks as unavailable.
6. Nonzero reviewer command blocks as unavailable.
7. Review result records reviewed SHA and raw output.

## Functional Requirements

- FR-001: Implement review request/result records.
- FR-002: Invoke only the configured reviewer command.
- FR-003: Bind review decision to candidate SHA.
- FR-004: Parse Approved and Changes Requested decisions deterministically.
- FR-005: Add CLI command for review run.
- FR-006: Add focused tests for approved, changes requested, unavailable, and nonzero reviewer.

## Non-Goals

- Replacing the independent reviewer decision.
- Applying fixes.
- Retrying review rounds.
- Publication.
- Exact HEAD gate implementation.

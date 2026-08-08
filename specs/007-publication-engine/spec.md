# Feature Specification: Publication Engine

**Feature Branch**: `codex/007-publication-engine-runtime`
**Status**: Draft
**Purpose**: Implement an executable publication gate that evaluates autonomous merge eligibility from explicit evidence and Execution Policy.

## User Story 1 - Evaluate autonomous merge readiness (P1)

An orchestrator can provide validation, review, SHA, repository, PR, and safety evidence and receive `PERMITTED` only when every autonomous merge condition is satisfied.

### Acceptance

1. Claude decision must be Approved.
2. Blocking findings must be none.
3. Validation must have passed.
4. Approved Review SHA, Validated SHA, Local HEAD, Remote Branch HEAD, and PR HEAD must match.
5. Exact HEAD Gate must be MATCH.
6. Primary Repository Audit must be SAFE.
7. Feature worktree must be clean.
8. PR base and head must match intended branches.
9. PR must be mergeable.
10. No unresolved blocking review state may exist.
11. No post-approval commit may exist.
12. No safety/recovery condition may be active.
13. Scope must remain approved.
14. Merge strategy must match Execution Policy.
15. Force push, history rewrite, bypass, or auto-merge bypass must not be required.

## Functional Requirements

- FR-001: Implement publication evidence and result records.
- FR-002: Evaluate all autonomous merge gate conditions deterministically.
- FR-003: Use Execution Policy publication merge strategy as the expected strategy.
- FR-004: Return `HUMAN_INTERVENTION_REQUIRED` with exact evidence on any false/uncertain condition.
- FR-005: Add focused tests for pass and representative failures.

## Non-Goals

- Creating PRs.
- Marking PRs ready.
- Merging PRs.
- Deleting branches or worktrees.
- Calling GitHub APIs.

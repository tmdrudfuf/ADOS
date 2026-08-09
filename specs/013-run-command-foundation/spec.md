# Feature Specification: ADOS Run Command Foundation

## Purpose

Add `ados run` as the first mutating user-facing workflow startup command.

## Functional Requirements

- FR-001: Provide `ados run --project <path> --feature <description>` with optional `--spec`, `--config`, `--json`, and `--dry-run`.
- FR-002: Load canonical project configuration and reuse Doctor, Status, Primary Guardian, and Worktree Lifecycle engines.
- FR-003: Classify run-start eligibility separately from generic Status blocking.
- FR-004: Block dirty primary, invalid config, branch/base ambiguity, conflicting Spec/worktree/branch, unsafe recovery, and inability to prove authoritative base.
- FR-005: Treat stale historical validation/review/archive evidence as informational when it does not prevent a new run.
- FR-006: Resolve explicit or automatic Spec numbers without overwriting existing Spec directories, branches, or worktrees.
- FR-007: Create and verify the feature worktree before any run record or implementation handoff file is written.
- FR-008: Persist a serializable workflow run record in the feature worktree.
- FR-009: Stop at `READY_FOR_IMPLEMENTATION`; do not invoke Codex, Claude, validation, PR, merge, or GitHub mutation.
- FR-010: Support read-only dry-run planning with zero mutations.

## Non-Goals

- Full autonomous feature lifecycle.
- Implementer invocation.
- Validation execution.
- Claude review.
- Publication or cleanup.

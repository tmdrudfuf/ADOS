# Feature Specification: ADOS Status Foundation

**Feature Branch**: `codex/012-status-foundation`
**Status**: Draft
**Purpose**: Add a read-only `ados status` command that reports evidence-backed ADOS-managed state for a target project.

## User Story 1 - Read project status

An orchestrator can run `ados status --project <path>` and receive a concise report of project readiness, repository state, worktrees, Specs, evidence-backed validation/review/publication state, and the next safe advisory action.

### Acceptance

1. Status reuses Doctor, Project Configuration, Execution Policy, Primary Repository Guardian, and read-only Git providers.
2. Status does not execute validation/review workflows or mutate repository/GitHub state.
3. Missing evidence is reported as `Unavailable` or `Unknown`, never inferred.
4. JSON output serializes the complete typed result.

## Functional Requirements

- FR-001: Add `ados status --project <path>` and `ados status <path>`.
- FR-002: Support `--json` and `--config`.
- FR-003: Introduce immutable serializable StatusResult.
- FR-004: Report repository branch/HEAD and Primary Guardian state.
- FR-005: Report all inspectable non-primary worktrees.
- FR-006: Resolve latest merged, active, and next unused Specs with evidence labels.
- FR-007: Report validation/review/exact-head/publication states only from SHA-bound evidence.
- FR-008: Surface human intervention reasons and an advisory next action.
- FR-009: Preserve read-only behavior and side-effect-free repeated execution.

## Non-Goals

- Implementing `ados run`, `ados resume`, or mutating workflow state.
- Executing validation commands.
- Invoking Codex or Claude.
- Creating, updating, or querying PRs through a new GitHub client.
- Repairing repositories or worktrees.

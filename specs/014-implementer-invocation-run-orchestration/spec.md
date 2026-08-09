# Feature Specification: Implementer Invocation / Run Orchestration

## Purpose

Extend `ados run` from durable startup to a bounded provider-neutral Implementer Runtime invocation.

## Functional Requirements

- FR-001: Consume an exact `READY_FOR_IMPLEMENTATION` workflow run record created after worktree verification.
- FR-002: Reconstruct project, worktree, branch, base SHA, Spec, roles, and policy context from durable evidence.
- FR-003: Invoke the configured implementer through a safe provider-neutral command boundary.
- FR-004: Require explicit executable/argv, exact cwd equal to feature worktree, bounded timeout, and no shell interpolation.
- FR-005: Reject stale context before spawn, including missing worktree, wrong branch, missing run record, primary contamination, role mismatch, and unsafe command shape.
- FR-006: Record immutable Implementer Runtime/Result/Evidence including stdout, stderr, exit code, timeout, cwd, command identity, resulting HEAD, and changed-file summary.
- FR-007: On successful process exit, verify postconditions before transitioning durable run state to `READY_FOR_VALIDATION`.
- FR-008: On failure, timeout, or blocked postcondition, persist truthful runtime evidence without starting validation, review, or publication.
- FR-009: Preserve dry-run zero-invocation behavior.
- FR-010: Repeated invocation for an exact completed run returns existing state/evidence without spawning again.

## Non-Goals

- Validation execution.
- Claude review.
- Exact Head Gate.
- Publication, PR, merge, deploy, or GitHub mutation.
- Full resume UX.

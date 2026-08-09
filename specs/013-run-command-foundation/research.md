# Research: ADOS Run Command Foundation

## Boundary

Existing ADOS engines provide safe readiness, status, guardian, and worktree operations. There is no provider-neutral implementer invocation boundary yet, so Spec013 stops at `READY_FOR_IMPLEMENTATION`.

## Eligibility

Status `BLOCKED` is too coarse for run startup. Historical stale review and validation evidence may be expected after previous merges and does not by itself prevent a new independent run. Dirty primary, invalid configuration, unsafe guardian state, base drift, and conflicting Spec ownership do block.

## Worktree Path

Without a project-specific worktree root model, the conservative default is a sibling of the primary repository named `<project-dir>-<feature-slug>`.

# Spec 005 — Independent Review Loop

## Goal

Define the bounded independent-review/fix loop used after validation and before Exact HEAD approval.

## Requirements

- Implementer/orchestrator and independent reviewer are distinct configured roles.
- Each review binds to one exact candidate SHA and explicit base/comparison scope.
- Valid `Changes Requested` findings return the workflow to implementation.
- After a fix, focused validation and full configured validation run again before a new review.
- Any changed candidate produces a new reviewed SHA; prior approval cannot be reused.
- Findings are classified as Valid-Fix, Duplicate, AlreadyFixed, OutOfScope, or RejectedWithEvidence.
- Rejected findings require concrete repository/spec evidence.
- Review rounds are bounded by project configuration, with a safe default maximum of 5.
- Repeated materially identical disagreement may stop for human resolution instead of causing an infinite loop.
- Reviewer timeout/failure may be retried according to recovery policy, but must never be converted to Approved.
- Review evidence is append-only and retains prompt/input, reviewer identity, exact SHA, decision, findings, round, and execution outcome.
- Internal/self-review may improve quality but cannot satisfy an independent-review gate.

## Outcomes

- Approved
- ChangesRequested
- ReviewerUnavailable
- ReviewLimitReached
- HumanDecisionRequired

## Non-goals

No provider CLI implementation or automatic GitHub publication in this Spec.
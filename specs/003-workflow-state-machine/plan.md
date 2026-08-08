# Plan — Workflow State Machine

Implement the workflow model as documentation-first normative state/transition definitions. Runtime code is deferred until the model stabilizes.

## Key rules

- States represent durable workflow facts, not UI screens.
- Guards are evaluated before side effects.
- A transition records actor, source state, target state, candidate SHA where relevant, decision, and reason.
- Invalid transition attempts never mutate the workflow state.
- Review/fix cycles are bounded by project configuration.
- Publication states are separate from implementation/review states.

## Validation

Cross-check every transition against Constitution authority and Spec 001 lifecycle. Ensure no path can skip worktree, validation, independent review, Exact HEAD, or configured human gates.
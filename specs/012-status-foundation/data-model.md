# Data Model: ADOS Status

## StatusResult

- status: `READY | ACTIVE | BLOCKED | IDLE | INVALID`
- project
- repository
- guardian
- workflow
- spec
- worktrees[]
- validation
- review
- exactHeadGate
- publication
- recovery
- nextAction

## Evidence State

Validation, review, exact-head, and publication status values are:

- `Passed | Failed | TimedOut | Approved | ChangesRequested | Match | Mismatch | Merged | Stale | Unavailable | Unknown`

Only values backed by explicit evidence are used.

## NextAction

- action
- reason
- evidence

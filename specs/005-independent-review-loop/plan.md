# Plan — Independent Review Loop

Define review orchestration as a provider-neutral policy contract. Provider adapters and executable orchestration arrive later.

## Decisions

- Review approval is SHA-scoped.
- Full validation precedes every final review candidate.
- Valid review fixes always produce renewed validation and renewed independent review.
- Finding disposition is explicit and append-only.
- Timeout/failure is a recovery state, never implicit approval.
- Default review cap is 5 rounds and is project-configurable.

## Validation

Cross-check against Constitution authority, Workflow State Machine, and Guardian Engine; specifically confirm that reviewer advice cannot authorize publication and that SHA changes invalidate approval.
# Spec 003 — Workflow State Machine

## Goal

Define the canonical ADOS feature lifecycle as an explicit state machine with legal transitions, guards, stop conditions, and evidence requirements.

## States

- Preflight
- WorktreeReady
- SpecReady
- ImplementationActive
- ValidationPassed
- ReviewPending
- ChangesRequested
- ReviewApproved
- ExactHeadVerified
- PublicationAuthorized
- DraftPublished
- ReadyAuthorized
- MergeAuthorized
- Merged
- CleanupAuthorized
- Archived
- Cleaned
- Blocked

## Requirements

- Feature writes cannot begin before WorktreeReady.
- SpecReady must precede implementation.
- ReviewPending requires a validated immutable candidate SHA.
- ChangesRequested returns to implementation without reusing stale approval.
- ReviewApproved must bind to the exact reviewed SHA.
- ExactHeadVerified requires reviewed SHA = validated SHA = current HEAD.
- Publication/Ready/Merge/Cleanup transitions require the configured human authority.
- Any failed guard transitions to Blocked without performing the guarded mutation.
- Historical transition evidence is append-only.

## Acceptance

A workflow engine can determine whether a requested transition is legal without relying on conversational inference.
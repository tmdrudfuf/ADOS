# Spec 008 — Recovery Engine

## Goal

Define deterministic recovery paths for common workflow failures without bypassing guards or silently expanding scope.

## Recovery classes

- DirtyPrimaryRepository
- DirtyFeatureWorktree
- ValidationFailure
- ReviewerTimeoutOrFailure
- ReviewDisagreement
- CandidateShaDrift
- BaseOrMergeBaseDrift
- PublicationFailure
- ArchiveOrCleanupFailure

## Requirements

- Recovery starts from the last valid durable workflow state.
- Recovery never relabels failed evidence as successful.
- Dirty primary recovery is analysis-first and uses targeted cleanup only after preservation is proven.
- Broad destructive cleanup commands are forbidden by default.
- Validation failure returns to implementation and requires renewed validation.
- Reviewer failure uses bounded retries; no valid reviewer decision means no approval.
- SHA drift requires renewed validation/review for the current candidate.
- Base drift is classified as material or non-material according to project policy; material reviewed-comparison drift blocks publication.
- Publication failures retain partial remote mutation evidence and do not repeat non-idempotent operations blindly.
- Cleanup requires archive proof and exact target identity.
- Unresolvable conflict stops for human decision.

## Non-goals

No automated shell/Git/GitHub recovery implementation in this Spec.
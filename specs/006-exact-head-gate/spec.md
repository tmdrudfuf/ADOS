# Spec 006 — Exact HEAD Gate

## Goal

Define one canonical invariant that prevents stale validation or stale review approval from authorizing publication after the candidate changes.

## Invariant

`approvedReviewSha == validatedSha == currentHead`

## Requirements

- The gate is evaluated immediately before any action that consumes review approval.
- Approved review evidence must identify an exact SHA.
- Validation evidence must identify the same exact SHA.
- Current feature HEAD must equal both evidence SHAs.
- Any tracked candidate change, amend, rebase, merge, cherry-pick, generated-file update, or documentation commit that changes HEAD invalidates the prior gate result.
- A mismatch never auto-rewrites evidence and never treats an ancestor approval as current.
- Recovery from mismatch requires renewed required validation and renewed independent review of the new candidate.
- Remote publication checks must additionally confirm the remote feature head is the approved candidate before later Ready/Merge gates consume approval.
- Gate results retain the three compared SHAs and a stable mismatch reason.

## Non-goals

No Git command execution or GitHub mutation in this Spec.
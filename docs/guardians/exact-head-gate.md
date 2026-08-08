# ADOS Exact HEAD Gate

The Exact HEAD Gate is the canonical freshness check for consuming independent review approval.

## Required equality

`approvedReviewSha == validatedSha == currentHead`

All three values must be present and identical.

## When to evaluate

Evaluate immediately before publication authorization is consumed, before Ready authorization is consumed, and before Merge authorization is consumed when those actions depend on prior review approval.

## Invalidation

Any new commit or tracked candidate change after validation/review invalidates the previous gate result. Examples include amend, rebase, merge, cherry-pick, documentation-only follow-up commits, generated-file updates, or review-record commits that are part of the candidate.

Do not treat an approved ancestor as approval for a descendant candidate.

## Recovery

On mismatch:

1. Block the guarded action.
2. Record the compared SHAs and mismatch code.
3. Run required validation for the current candidate.
4. Obtain fresh independent review for that candidate.
5. Re-evaluate the gate.

## Remote consumption

Once a feature branch is published, later remote gates should also prove the remote feature head equals the approved candidate. Remote equality supplements rather than replaces the three-way local invariant.
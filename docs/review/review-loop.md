# ADOS Independent Review Loop

## Roles

The implementer/orchestrator prepares and fixes the candidate. The independent reviewer evaluates it. The same runner/identity must not satisfy both roles when the active project requires independent review.

## Candidate contract

Every review request identifies:

- feature id
- authoritative base SHA/ref
- exact candidate SHA
- Spec/Plan/Tasks paths
- changed-file scope
- validation evidence
- previous finding ledger when relevant

A reviewer decision applies only to that candidate SHA.

## Loop

1. Validate the candidate.
2. Record `validatedSha`.
3. Invoke the configured independent reviewer against that exact SHA.
4. Persist decision and findings.
5. If Approved, proceed to Exact HEAD verification.
6. If Changes Requested, classify each finding.
7. Fix valid blocking findings only.
8. Rerun focused validation and full configured validation.
9. Create/identify a new immutable candidate SHA.
10. Start a fresh independent review round.

## Finding classification

- `ValidFix`: concrete in-scope defect; fix it.
- `Duplicate`: materially identical to a previously recorded finding.
- `AlreadyFixed`: evidence shows current candidate no longer contains the defect.
- `OutOfScope`: requires work beyond the active Spec and is not necessary for correctness/safety of the current feature.
- `RejectedWithEvidence`: conflicts with repository precedent, active Spec, or higher authority; record concrete evidence.

A rejected finding is not silently discarded.

## Stopping conditions

Stop and require human attention when:

- configured maximum review rounds are exhausted (default 5)
- the same material dispute repeats after evidence-backed resolution
- a finding requires a new architecture/product decision
- validation cannot be restored within feature scope
- an independent reviewer remains unavailable after configured recovery attempts

## Timeout/failure

Timeout, provider failure, malformed output, or missing decision are not approval. Recovery may retry with bounded attempts and retained evidence. If no valid review result is produced, return `ReviewerUnavailable`.

## Approval freshness

Any candidate change after approval invalidates that approval. Before consuming approval, ShaGuardian must prove:

`approvedReviewSha == validatedSha == currentHead`

## Review evidence

Review evidence is append-only. Preserve round, reviewer identity/provider, exact candidate, base, prompt/context reference, execution status, decision, findings, dispositions, timestamps, and any timeout/failure details.
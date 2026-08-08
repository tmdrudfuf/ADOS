# ADOS Recovery Engine

Recovery never bypasses the failed guard. It gathers new evidence, restores a valid state when safe, and re-enters the canonical workflow.

## Dirty primary repository

1. Stop feature work.
2. Inspect exact tracked/untracked differences.
3. Identify whether data is authoritative, duplicated, stale, or unrelated.
4. Prove preservation before any deletion/restoration.
5. Use explicit targeted restore/delete commands only.
6. Never default to broad `reset --hard` or `clean -fdx` recovery.
7. Re-run PrimaryRepositoryGuardian.

## Dirty feature worktree

Classify changes as intended feature work, generated artifacts, unrelated user work, or incomplete recovery state. Never discard ambiguous work. Resume only after scope is explicit.

## Validation failure

Return to implementation, fix the defect, rerun focused checks, then rerun the complete configured validation set. Previous validation evidence becomes stale.

## Reviewer timeout/failure

Retry only within configured bounded attempts. Preserve every attempt. Timeout, malformed output, and provider error remain non-approved outcomes. If exhausted, stop with ReviewerUnavailable.

## Review disagreement

Record the repeated finding and implementation disposition. If concrete repository/spec evidence does not resolve the disagreement, stop for a human architecture/scope decision rather than looping indefinitely.

## Candidate SHA drift

Invalidate validation/review evidence for the old candidate. Validate and independently review the new candidate before consuming approval.

## Base or merge-base drift

Recompute the reviewed comparison. If drift changes the relevant diff or project policy treats it as material, renew validation/review before publication.

## Publication failure

Inspect the remote result before retry. Determine whether the operation partially succeeded. Prefer idempotent lookup/verification before repeating a mutation.

## Archive/cleanup failure

Do not delete worktrees/branches when required evidence is not archived or target identity is uncertain. Retry archival/verification first.

## Human escalation

Escalate when recovery would require destructive ambiguity resolution, scope expansion, architecture change, exceeded bounded attempts, or an authority override.
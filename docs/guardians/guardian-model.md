# ADOS Guardian Model

Guardians are independent safety checks that return Pass or Block with evidence. A guardian does not silently repair state unless a later recovery policy explicitly authorizes a narrow repair.

## Primary Repository Guardian

Checks primary path, branch, HEAD relationship, staged files, tracked modifications, and allowed local untracked paths. Unexpected tracked change blocks before feature writes and publication.

## Worktree Guardian

Checks active directory, worktree registration, feature branch, expected base lineage, and isolation from the primary checkout. Writing while the active directory is the primary checkout blocks immediately.

## Git Guardian

Checks intended branch, base, staged scope, current HEAD, and forbids unsafe force/destructive operations by default.

## Validation Guardian

Binds validation evidence to one SHA and to the configured command set. A later commit makes validation stale.

## Review Guardian

Binds reviewer identity, review decision, and findings to one exact SHA. Implementer self-review cannot satisfy independent-review requirements.

## SHA Guardian

Enforces `approvedReviewSha == validationSha == currentHeadSha` before publication readiness can be claimed.

## Publication Guardian

Checks explicit authorization for each remote state transition and verifies target base/head before mutation. Conditional autonomous merge requires every configured autonomous merge gate to pass; otherwise the publication guardian returns `HUMAN_INTERVENTION_REQUIRED` with evidence.

## Cleanup Guardian

Requires review-artifact archival and explicit cleanup authorization before branch/worktree/remote deletion unless cleanup is proven safe, narrowly scoped, post-merge, and covered by the Conditional Autonomous Merge Authority. Cleanup is narrow and evidence-backed.

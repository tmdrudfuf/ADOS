# ADOS Foundation Contract

## Primary Repository Contract

The primary checkout is an observation and comparison point during feature development. Feature writes must not occur there.

Before the first feature write, ADOS must prove:

1. a dedicated feature branch exists;
2. a dedicated worktree exists;
3. the active working directory equals that worktree;
4. the active branch equals the intended feature branch;
5. the primary checkout has no unexpected tracked modifications.

Failure blocks the workflow before Spec creation.

## Review Contract

An independent review decision applies only to the exact reviewed SHA. `Approved` is valid for publication gating only when:

`approvedReviewSha == validationSha == currentHeadSha`.

## Publication Contract

Implementation/review completion alone authorizes no remote or destructive mutation. Push, Ready, merge, branch deletion, worktree deletion, remote deletion, and deploy require explicit policy/human authorization.

## Cleanup Contract

Cleanup must be narrowly targeted and evidence-backed. Broad destructive cleanup is forbidden by default.

## Truthfulness Contract

ADOS must never represent unavailable independent review as approval, stale review evidence as current, or an unverified repository state as safe.

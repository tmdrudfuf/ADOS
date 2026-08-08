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

Implementation/review completion alone authorizes no remote or destructive mutation. Push, Ready, merge, branch deletion, worktree deletion, remote deletion, and deploy require explicit policy/human authorization unless the Conditional Autonomous Merge Authority is fully satisfied.

Autonomous merge is permitted only when:

1. Independent Claude review decision is Approved.
2. Blocking findings are None.
3. Full validation passed on the exact candidate HEAD.
4. Approved Review SHA = Validated SHA = Local HEAD = Remote Branch HEAD = PR HEAD.
5. Exact HEAD Gate is MATCH.
6. Primary Repository Audit is SAFE.
7. Feature worktree is clean.
8. PR base and head are exactly the intended branches.
9. PR has no merge conflicts.
10. No unresolved blocking review state exists.
11. No new commit appeared after independent approval.
12. No safety/recovery condition is active.
13. The feature remained within the approved Spec scope.
14. Merge uses the repository's configured standard merge strategy.
15. Force push, auto-merge bypass, or history rewriting is not required.

If every condition is true, Codex may push the reviewed feature branch, create a Draft PR, verify the PR, mark it Ready for Review, perform one final Exact HEAD Gate, merge the PR, verify the merge, update local main, archive required review artifacts, perform proven-safe post-merge cleanup, and begin the next roadmap Spec. No additional human confirmation is required.

If any condition is false or uncertain, stop. Do not merge. Return `HUMAN_INTERVENTION_REQUIRED` with exact evidence.

## Cleanup Contract

Cleanup must be narrowly targeted and evidence-backed. Broad destructive cleanup is forbidden by default. Post-merge cleanup may be autonomous only when it is proven safe, narrowly scoped, and covered by the Conditional Autonomous Merge Authority; otherwise it remains human-only.

## Truthfulness Contract

ADOS must never represent unavailable independent review as approval, stale review evidence as current, or an unverified repository state as safe.

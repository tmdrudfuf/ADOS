# ADOS Core Constitution

## Authority

Humans own publication and destructive-operation authority. AI agents operate only inside explicit workflow and project-policy boundaries, including the Conditional Autonomous Merge Authority when every required gate is satisfied.

## Primary Repository

The primary checkout is read-only during feature work. It may be inspected, compared, and audited, but no feature Spec, pointer update, implementation, test, or generated artifact may be written there.

The sole exception is a one-time empty-repository bootstrap commit required to create repository history.

## Worktree-First Rule

Every feature must create and verify a dedicated branch/worktree before the first feature write. If current directory, branch, worktree identity, or primary-repository status is ambiguous, stop before writing.

## Spec-First Rule

Spec -> Plan -> Tasks precede implementation. Later documentation may be updated to reflect valid implementation discoveries, but implementation must not begin from an undefined feature boundary.

## Independent Review

The implementer/orchestrator cannot substitute self-review for the configured independent reviewer. Review approval belongs to one exact SHA.

## Exact HEAD Gate

A review-approved candidate exists only when:

`Approved Review SHA = Validated SHA = Current HEAD`.

Any tracked change or commit after approval invalidates the gate.

## Human Publication Boundary

Unless an exact operation has been explicitly authorized or the Conditional Autonomous Merge Authority is fully satisfied, AI agents do not push, mark Ready, approve, merge, deploy, delete branches, delete worktrees, or delete remote branches.

## Conditional Autonomous Merge Authority

The project-configured execution policy permits Codex to merge an ADOS feature PR without an additional per-PR confirmation only when every autonomous merge gate below is satisfied.

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

## Narrow Mutation and Cleanup

Mutations must be explicit and scoped. Broad destructive operations such as unrestricted hard reset, clean, or wildcard deletion are not default recovery mechanisms. Post-merge cleanup may be autonomous only when it is proven safe, narrowly scoped, and covered by the Conditional Autonomous Merge Authority; otherwise it remains human-only.

## Truthfulness

Unknown, unavailable, stale, or failed evidence must be reported as such. ADOS never upgrades uncertainty into success.

# ADOS Agent Instructions

## Purpose

ADOS defines reusable governance and workflow rules for AI-assisted software development. Treat this repository as infrastructure: changes must be explicit, reviewable, provider-neutral, and safe by default.

## Canonical roles

- Implementer / Orchestrator: Codex CLI
- Independent Reviewer: Claude CLI
- Human: authority for publication and destructive operations, except where the Conditional Autonomous Merge Authority is fully satisfied

## Primary repository protection

The primary checkout of a project using ADOS is read-only during feature work. Never create specs, implementation files, or pointer updates in the primary checkout. Create and enter a dedicated feature worktree first, verify the current directory and branch, then perform all writes there.

## Feature workflow

Preflight -> create worktree -> verify worktree -> Spec -> Plan -> Tasks -> Implement -> focused validation -> full validation -> local commit -> independent review -> bounded fix/re-review loop -> Exact HEAD Gate -> conditional autonomous merge gate or human publication gate.

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

## Human-only boundaries

Unless a human explicitly authorizes the exact operation or the Conditional Autonomous Merge Authority is fully satisfied, do not push, mark a PR ready, approve, merge, delete branches, delete worktrees, delete remote branches, deploy, or perform unrelated remote mutations.

## Review rules

The implementer may orchestrate review but may not substitute self-review for the independent reviewer. Any change after approval invalidates that approval until the new exact HEAD is revalidated and independently reviewed.

## Repository safety

Avoid broad destructive cleanup such as `git reset --hard`, `git clean -fd`, `git clean -fdx`, or wildcard deletion. Prefer narrow, evidence-backed operations. Post-merge cleanup may be autonomous only when it is proven safe, narrowly scoped, and covered by the Conditional Autonomous Merge Authority; otherwise it remains human-only.

## Spec 001

For repository-foundation details, read `specs/001-repository-foundation/plan.md`.

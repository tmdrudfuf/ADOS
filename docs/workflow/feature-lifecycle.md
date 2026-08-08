# ADOS Feature Lifecycle

## Canonical states

1. Preflight
2. Primary Audit
3. Feature Branch/Worktree Creation
4. Worktree Verification
5. Spec
6. Plan
7. Tasks
8. Implementation
9. Focused Validation
10. Full Validation
11. Local Commit
12. Independent Review
13. Fix/Re-review Loop
14. Exact HEAD Gate
15. Conditional Autonomous Merge Gate or Human Publication Gate
16. Push + Draft PR when authorized
17. Ready/Merge Decision
18. Archive + Cleanup after authorization or proven-safe autonomous gate

## Preconditions

No transition may silently assume its predecessor succeeded. Each phase consumes explicit evidence from the previous phase.

## Primary-audit checkpoints

Check the primary checkout at minimum:

- before feature worktree creation;
- before the first feature write;
- before publication;
- before cleanup completion.

Unexpected tracked changes block progression.

## Review loop

A Changes Requested decision may cause a bounded fix loop. The implementer evaluates each finding, fixes valid in-scope blockers, reruns validation, commits, then requests a fresh review of the new exact HEAD. Historical approval never carries forward.

## Stop conditions

Stop when:

- required repository state is ambiguous;
- primary checkout is unexpectedly dirty;
- validation cannot pass without expanding scope;
- independent review is unavailable after configured recovery policy;
- review findings require a human architecture decision;
- Exact HEAD Gate mismatches;
- a requested remote/destructive action lacks authorization;
- any Conditional Autonomous Merge Authority condition is false or uncertain.

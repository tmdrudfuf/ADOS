# Quickstart: ADOS Repository Foundation

1. Clone the project repository that will use ADOS.
2. Treat its primary checkout as read-only during feature work.
3. Run preflight and record the authoritative base SHA.
4. Create a dedicated feature branch and worktree before any write.
5. Verify current directory, branch, worktree path, and primary status.
6. Create Spec -> Plan -> Tasks inside the feature worktree.
7. Implement only after planning artifacts are coherent.
8. Run focused and full validation.
9. Commit locally.
10. Invoke the independent reviewer against the exact validated HEAD.
11. If Changes Requested, fix valid findings, revalidate, recommit, and review the new exact HEAD.
12. Require `approvedReviewSha == validationSha == currentHeadSha`.
13. Stop at the human publication gate unless exact operations were explicitly authorized.

Never create a feature Spec in the primary checkout before the feature worktree exists.
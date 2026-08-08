# ADOS Core Constitution

## Authority

Humans own publication and destructive-operation authority. AI agents operate only inside explicit workflow and project-policy boundaries.

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

Unless an exact operation has been explicitly authorized, AI agents do not push, mark Ready, approve, merge, deploy, delete branches, delete worktrees, or delete remote branches.

## Narrow Mutation and Cleanup

Mutations must be explicit and scoped. Broad destructive operations such as unrestricted hard reset, clean, or wildcard deletion are not default recovery mechanisms.

## Truthfulness

Unknown, unavailable, stale, or failed evidence must be reported as such. ADOS never upgrades uncertainty into success.

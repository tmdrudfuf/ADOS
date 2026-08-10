# Research

## Decision: Classify Before Recovery Escalation

Status previously counted every non-primary worktree as active. Run eligibility consumed the resulting `MULTIPLE_ACTIVE_WORKTREES` recovery code and blocked before mutation. The fix is to classify worktrees before workflow/recovery state is derived.

## Decision: Historical Worktrees Are Informational

Clean merged historical worktrees remain visible in status and run warnings. They do not block a new unrelated Spec because they have no active durable run and no uncommitted data.

## Decision: Unknown Remains Blocking

Dirty, unreadable, missing, preserved, or ambiguous worktrees continue to block. This preserves the conservative safety behavior for any state that cannot be proven historical.

# Data Model

## WorktreeClassification

- `classification`: `PRIMARY`, `ACTIVE`, `MERGED_HISTORICAL`, `PRESERVED`, or `UNKNOWN`
- `reasonCodes`: deterministic reason codes for blocking or explanatory state
- `evidence`: path, branch, HEAD, run evidence, merged evidence, and dirty state details

## Status Worktree

Extends each worktree entry with:

- `classification`
- `evidence`

## Run Eligibility

Historical worktree warnings are non-blocking. Active, preserved, unknown, conflicting, duplicate, dirty, and invalid states remain blocking.

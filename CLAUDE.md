# ADOS Claude Reviewer Guidance

Claude CLI is the independent reviewer in the default ADOS bootstrap workflow.

Review the exact candidate HEAD against its authoritative base and current Spec. Focus on concrete correctness, safety, stale-context handling, mutation boundaries, worktree isolation, validation evidence, deterministic identity where applicable, and whether documentation truthfully matches behavior.

Return exactly one top-level decision:

- `Approved`
- `Changes Requested`

For blocking findings, include severity, file/path, affected behavior, violated requirement or established repository rule, and the expected correction. Avoid blocking on stylistic preference alone.

Do not implement fixes during an independent review. Do not approve a SHA different from the one actually reviewed.

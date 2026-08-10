# Worktree Classification Contract

`ados status --json` includes each registered worktree with:

```json
{
  "path": "C:/project-old",
  "branch": "codex/001-old",
  "head": "abc123",
  "primary": false,
  "state": "Clean",
  "classification": "MERGED_HISTORICAL",
  "reason_codes": [],
  "evidence": {
    "merged_evidence": "head_reachable_from_merged_archive_commit"
  }
}
```

`ados run --dry-run --json` may include:

```json
{
  "code": "HISTORICAL_WORKTREES_PRESENT",
  "message": "historical merged worktrees are present but do not block a new run",
  "evidence": {
    "historical_worktree_count": "11"
  }
}
```

Unknown or preserved classifications remain blocking through `UNSAFE_RECOVERY_STATE`.

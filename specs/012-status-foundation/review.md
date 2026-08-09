# Review: ADOS Status Foundation

## Focused Validation

- `python -m unittest tests.test_cli_status tests.test_cli_doctor`
  - Result: PASS
  - Coverage: Status CLI parsing, JSON output, exit codes, guardian blocking, allowed local paths, worktree reporting, Spec resolution, validation/review evidence staleness, Exact HEAD status, publication evidence, Windows path handling, repeated read-only execution, and Doctor regression coverage.

## AIverse Read-Only Smoke

- Target: `C:\Users\tmdru\Desktop\Ky-Project\AIverse`
- Before HEAD: `68ae3701ec2c1c4bd34104efaa8c54a94b22e9da`
- After HEAD: `68ae3701ec2c1c4bd34104efaa8c54a94b22e9da`
- Before status: clean
- After status: clean
- `.claude/`: present before and after
- Human output: `BLOCKED` because Primary Guardian was `SAFE` but multiple registered non-primary worktrees were present and SHA-bound archive evidence was stale for the current main.
- JSON output: included Guardian `SAFE`, publication `Merged`, Exact HEAD Gate `Unavailable` for historical merged feature-gate evidence, and machine-readable recovery reason codes.

## Full Validation

- `python -m unittest discover`
  - Result: PASS
  - Tests: 96
- `python -m ados validation run --policy tests/fixtures/execution-policy.valid.json --repo .`
  - Result: PASS
  - Commands executed by engine: `python -m unittest discover`, `git diff --check`
- `git diff --check`
  - Result: PASS
- `git diff --cached --check`
  - Result: PASS

## Independent Review

### Round 1

- Reviewed SHA: `a33ae1b494e24384dfe9184eab3c0f14dbd0f3ca`
- Decision: Changes Requested
- Blocking finding: `latest_merged_spec` could be reported from any archive evidence, even when the archive did not prove a merge.
- Disposition: Valid. Status now reports `latest_merged_spec` only when archive evidence includes `merge_commit` equal to the current repository HEAD. Added regression coverage for an unmerged `Changes Requested` archive.

### Round 2

- Reviewed SHA: `c636619c2ea7a5113f2b55d25782ad8fb05270b7`
- Decision: Changes Requested
- Blocking finding: requiring `merge_commit == current HEAD` made `latest_merged_spec` unavailable after any later commit, even when the recorded merge commit remained reachable from current HEAD.
- Disposition: Valid. Added a read-only Git ancestry check and resolved latest merged Spec from archive merge commits that are ancestors of the current HEAD. Validation/review/exact-head/publication remain exact-SHA scoped.

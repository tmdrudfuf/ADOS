# Requirements Checklist

- [x] Worktree lifecycle inputs are explicit.
- [x] Create is gated by Primary Repository Guardian.
- [x] Verify detects missing or unregistered worktrees.
- [x] Verify detects branch mismatch.
- [x] Remove is gated by cleanup autonomy.
- [x] Remove only targets the explicit worktree path.
- [x] No branch or remote deletion is implemented.
- [x] Lifecycle results use deterministic violation codes.

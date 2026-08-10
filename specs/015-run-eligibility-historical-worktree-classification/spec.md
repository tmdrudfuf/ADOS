# Spec 015 - Run Eligibility Historical Worktree Classification

## Purpose

Fix the production false-positive where `ados run` blocks new work solely because multiple historical merged worktrees remain registered.

## Requirements

- Classify non-primary worktrees using evidence, not path names alone.
- Treat clean merged historical worktrees as non-blocking for new run startup.
- Continue blocking active, preserved, dirty, unknown, duplicate, and conflicting worktrees.
- Update `ados status` so active, historical, preserved, and unknown worktrees are distinguishable.
- Keep `ados doctor` read-only and side-effect free.
- Preserve dry-run zero-mutation behavior.

## Non-Goals

- Cleaning old worktrees.
- Deleting branches or remote branches.
- Changing publication, validation, review, or implementer runtime behavior.
- Starting AIverse Spec 084.

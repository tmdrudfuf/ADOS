# Spec 009 — AIverse Project Adapter

## Goal

Define AIverse as the first concrete ADOS project adapter without hardcoding AIverse-specific paths or validation commands into ADOS core policy.

## Requirements

- Adapter declares project identity, primary repository path, allowed local-only artifacts, worktree naming convention, branch naming convention, validation commands, implementer/reviewer bindings, review limits, archive destination/policy, and human-only publication/cleanup stages.
- AIverse adapter uses Codex CLI as Implementer/Orchestrator and Claude CLI as Independent Reviewer.
- AIverse validation baseline includes `npm test`, `npx tsc --noEmit`, `npm run build`, `git diff --check`, and `git diff --cached --check`.
- AIverse primary repository may explicitly allow local `.claude/` while still blocking unexpected tracked mutation.
- Feature development must create/enter the feature worktree before Spec Kit writes.
- Review fix loop is bounded and SHA-scoped.
- Push/Draft, Ready, Merge, archive, worktree deletion, local/remote branch deletion follow separately configured human gates.
- Adapter configuration cannot weaken ADOS constitutional safety rules.

## Acceptance

A future ADOS runner can load the AIverse adapter and derive project-specific commands/paths without changing reusable core documents.
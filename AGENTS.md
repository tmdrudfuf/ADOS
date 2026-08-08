# ADOS Agent Instructions

## Purpose

ADOS defines reusable governance and workflow rules for AI-assisted software development. Treat this repository as infrastructure: changes must be explicit, reviewable, provider-neutral, and safe by default.

## Canonical roles

- Implementer / Orchestrator: Codex CLI
- Independent Reviewer: Claude CLI
- Human: authority for publication and destructive operations

## Primary repository protection

The primary checkout of a project using ADOS is read-only during feature work. Never create specs, implementation files, or pointer updates in the primary checkout. Create and enter a dedicated feature worktree first, verify the current directory and branch, then perform all writes there.

## Feature workflow

Preflight -> create worktree -> verify worktree -> Spec -> Plan -> Tasks -> Implement -> focused validation -> full validation -> local commit -> independent review -> bounded fix/re-review loop -> Exact HEAD Gate -> human publication gate.

## Human-only boundaries

Unless a human explicitly authorizes the exact operation, do not push, mark a PR ready, approve, merge, delete branches, delete worktrees, delete remote branches, deploy, or perform unrelated remote mutations.

## Review rules

The implementer may orchestrate review but may not substitute self-review for the independent reviewer. Any change after approval invalidates that approval until the new exact HEAD is revalidated and independently reviewed.

## Repository safety

Avoid broad destructive cleanup such as `git reset --hard`, `git clean -fd`, `git clean -fdx`, or wildcard deletion. Prefer narrow, evidence-backed operations.

## Spec 001

For repository-foundation details, read `specs/001-repository-foundation/plan.md`.
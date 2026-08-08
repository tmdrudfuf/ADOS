# ADOS

AI Development Operating System (ADOS) is a reusable, provider-neutral workflow framework for AI-assisted software development.

This repository is being bootstrapped with a spec-first process. The first feature establishes repository structure, governance, worktree safety, validation, review, publication boundaries, and reusable project configuration.

## Bootstrap note

Because the repository was initially empty, this README is the one-time bootstrap commit on `main`. All feature development after this bootstrap must occur on a dedicated feature branch/worktree before any Spec Kit or implementation files are created.

## Initial roles

- Implementer / Orchestrator: Codex CLI
- Independent Reviewer: Claude CLI
- Human: publication and destructive-operation authority, except when every Conditional Autonomous Merge Authority gate is satisfied

## Core principles

- Primary repository protection
- Worktree-only feature development
- Spec-first delivery
- Provider-neutral architecture
- Independent review
- Exact-HEAD validation and review gates
- Conditional autonomous merge with human fallback
- Proven-safe post-merge cleanup only

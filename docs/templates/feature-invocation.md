# ADOS Feature Invocation Contract

## Minimal invocation

A user should normally provide only the target project plus either an explicit feature identity or a feature description.

Examples:

```text
Project: AIverse
Feature: Spec 082 — Validation Runtime Foundation
```

```text
Project: AIverse
Feature: Add employee scheduling with availability-aware assignment.
```

## Resolution

ADOS resolves:

- project adapter
- authoritative repository/base
- Spec number when omitted
- feature slug
- branch name
- worktree path
- applicable Constitution/workflow/guardian/review/recovery/publication policy
- validation commands and role bindings

Repository/project evidence is authoritative over conversational assumptions.

## Authorization separation

Feature invocation authorizes feature planning/implementation only within the active project policy. It does not automatically authorize Push, Draft PR, Ready, Merge, cleanup, force operations, deployment, or unrelated remote mutation.

## Write boundary

No Spec Kit or implementation write may occur until Preflight passes, the feature worktree is created, the agent enters that worktree, and WorktreeGuardian confirms the location and feature branch.

## Evidence

Persist the raw request plus resolved project id, Spec number, title/slug, authoritative base, branch, and worktree identity so later reports do not depend on chat history.
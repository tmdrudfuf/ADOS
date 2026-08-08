# Spec 002 — Constitution Authority

## Goal

Define the normative authority model that governs ADOS workflows and prevents an agent, reviewer, project adapter, or automation from silently overriding repository-safety and human-control rules.

## Requirements

- Constitution rules are normative and take precedence over workflow convenience.
- Project adapters may narrow behavior but may not weaken constitutional safety boundaries.
- Human publication/destructive-action authority must be explicit.
- Primary-repository protection, worktree-first writes, Exact HEAD review, and immutable review history are mandatory defaults.
- Conflicting instructions must resolve by authority order rather than agent preference.
- Any constitutional exception must be explicit, scoped, attributable to a human decision, and non-persistent unless separately adopted.

## Non-goals

- Runtime implementation.
- Provider-specific CLI execution.
- GitHub automation.
- Review execution.

## Acceptance

A reader can determine which instruction wins when Constitution, workflow, project configuration, reviewer advice, and feature instructions conflict.
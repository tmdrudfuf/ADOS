# ADOS Independent Review Policy

## Default bootstrap roles

- Implementer / Orchestrator: Codex CLI
- Independent Reviewer: Claude CLI

Projects may configure different adapters, but implementer and independent reviewer must remain distinct for a review to satisfy this policy.

## Decision contract

The reviewer returns one top-level decision:

- Approved
- Changes Requested

If the configured reviewer cannot run or produces no trustworthy decision, record review as unavailable rather than approved.

## Findings

Blocking findings should identify severity, location, affected behavior, violated requirement/established rule, and expected correction. Non-blocking findings must not be silently promoted to blockers.

## Bounded loop

Projects configure a maximum review-round count. Each Changes Requested round is recorded. The implementer may reject a finding only with concrete repository/spec evidence. Valid blockers are fixed, validation is rerun, a new commit is created, and the reviewer inspects the new exact HEAD.

## SHA binding

Approval is valid only for the reviewed SHA. It cannot be reused after any tracked change or new commit.

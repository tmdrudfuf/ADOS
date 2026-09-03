# ADOS Independent Review Policy

## Default bootstrap roles

- Implementer / Orchestrator: Codex CLI
- Independent Reviewer: Claude CLI

Projects may configure different adapters, but implementer and independent reviewer must remain distinct for a review to satisfy this policy.

When `execution_policy.agent_roles` enables adaptive selection, the implementer
and reviewer are chosen from operator-defined preference lists plus verified
runtime failure classification (never invented usage numbers). The pair is
persisted durably and preserved across resume. If Codex implements a candidate
(for example after failover), Codex cannot review it and Claude reviews; if
Claude implements, Codex reviews. If no independent reviewer can be selected,
review is `REVIEW_BLOCKED` and publication is blocked — implementer self-review
is never substituted.

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

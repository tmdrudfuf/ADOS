# Spec 010 — Feature Invocation API

## Goal

Define the minimal user-facing input contract that lets ADOS derive a feature lifecycle without requiring a giant per-feature prompt.

## Inputs

ADOS accepts either:

- an explicit Spec number plus feature title/description, or
- a feature description without a Spec number, in which case the next unused project Spec number is resolved from project state.

## Requirements

- Invocation identifies the target project adapter.
- Explicit Spec numbers must not reuse an existing Spec number.
- Implicit numbering must resolve from authoritative project state, not conversation memory alone.
- Feature input may add scope/constraints but cannot weaken Constitution or project safety policy.
- Invocation does not itself authorize publication, Ready, Merge, cleanup, or destructive operations.
- ADOS derives branch/worktree names from project configuration and the resolved feature slug.
- Before any feature write, ADOS completes Preflight and reaches WorktreeReady.
- ADOS records the original user feature request and resolved feature identity as workflow evidence.
- Ambiguity that can be resolved from repository/project configuration should be resolved automatically; material unresolved product/architecture ambiguity may stop before mutation.

## Examples

`Implement Spec 082 — Validation Runtime Foundation`

`Add a project scheduling dashboard with employee availability.`

## Non-goals

No CLI parser implementation in this Spec.
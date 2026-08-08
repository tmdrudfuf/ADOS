# Spec 007 — Publication Engine

## Goal

Define remote publication as explicit, separately authorized stages so one human decision cannot silently authorize later GitHub mutations.

## Stages

- PushAuthorized
- DraftPrAuthorized
- ReadyAuthorized
- MergeAuthorized

## Requirements

- Publication requires Exact HEAD Gate success for the candidate being published.
- Push authorization does not imply Ready or Merge authorization.
- Draft PR creation is the default publication surface after push unless project configuration says otherwise.
- Ready for Review requires a separate explicit human decision when configured human-only.
- Merge requires a separate explicit human merge decision.
- Force push, auto-merge, remote branch deletion, and unrelated GitHub mutations are not implied by any publication authorization.
- Before Ready/Merge, verify remote feature head still equals the approved reviewed candidate.
- Base/ref drift that changes the reviewed comparison must block and require renewed validation/review as configured.
- Every remote mutation records target repository, branch/PR, candidate SHA, actor/authorization evidence, and result.

## Non-goals

No GitHub API implementation in this Spec.
# Research: Implementer Invocation / Run Orchestration

## Command Boundary

Existing validation/review engines use shell strings. Spec014 introduces a stricter implementer boundary: parse configured adapter into executable and argv, run with `shell=False`, and reject shell metacharacters before spawn.

## Scope

No clean provider-neutral orchestration layer exists for validation/review/publication composition, so this Spec stops at `READY_FOR_VALIDATION`.

## Test Strategy

Automated tests use deterministic Python fixture runners instead of invoking real Codex. This proves subprocess behavior without nondeterministic AI workload or external API usage.

# Plan — Constitution Authority

## Design

Add a single normative authority document under `docs/constitution/` and keep project-specific configuration subordinate to it.

Authority order:

1. Human explicit decision for the current action
2. ADOS Constitution
3. ADOS workflow/recovery/publication rules
4. Project adapter/configuration
5. Feature Spec/Plan/Tasks
6. Reviewer recommendations
7. Agent implementation preference

A lower layer may add constraints, but it cannot remove a higher-layer safety requirement.

## Validation

- Cross-check against Spec 001 Constitution and lifecycle docs.
- Confirm no project-specific absolute paths enter the reusable core.
- Confirm human-only destructive/publication boundaries remain explicit.

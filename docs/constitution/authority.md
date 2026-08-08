# ADOS Constitution Authority Model

## Normative order

When instructions conflict, use this order:

1. Explicit human decision for the current action
2. ADOS Constitution
3. ADOS workflow, guardian, recovery, publication, and cleanup rules
4. Project adapter and project configuration
5. Current feature Spec, Plan, and Tasks
6. Independent reviewer recommendations
7. Implementer/orchestrator preference

Lower layers may be stricter. They may not weaken higher-layer safety requirements.

## Human authority

Human authorization is required for actions designated human-only by the active Constitution or project configuration. Authorization must identify the relevant action or clearly cover the current publication stage. Silence is not authorization.

## Project configuration

A project adapter may define repository paths, validation commands, allowed local artifacts, provider bindings, and additional safety constraints. It may not silently disable primary-repository protection, exact-HEAD review requirements, independent review, or destructive-action gates.

## Reviewer authority

A reviewer identifies defects and recommends changes. Reviewer findings do not automatically rewrite the Constitution, change feature scope, authorize publication, or authorize destructive operations. The orchestrator must classify findings against repository evidence and the active authority stack.

## Feature authority

A feature Spec governs the feature's product behavior and scope. It cannot authorize an action forbidden by a higher authority layer.

## Exceptions

A constitutional exception must be explicit, human-authorized, scoped to a concrete action or feature, and recorded in workflow evidence. An exception does not become a permanent rule merely because it was used once.

## Conflict behavior

When a conflict cannot be resolved from the authority stack, stop before mutation and report the conflicting instructions and the highest applicable authority.
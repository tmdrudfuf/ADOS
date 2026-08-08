# Feature Specification: ADOS Repository Foundation

**Feature Branch**: `codex/001-repository-foundation`  
**Status**: Draft  
**Purpose**: Establish the minimum durable repository, governance, safety, workflow, and review structure required to evolve ADOS safely.

## User Story 1 - Start every feature from an isolated worktree (P1)

A developer can use ADOS without risking accidental writes in the primary project checkout.

### Acceptance

1. The primary checkout is treated as read-only for feature work.
2. A dedicated feature worktree and branch are created before any Spec Kit or implementation write.
3. Current directory, branch, and worktree identity are verified before the first write.
4. Any unexpected tracked change in the primary checkout blocks feature progress until investigated.

## User Story 2 - Run a repeatable spec-to-review workflow (P1)

A developer can follow one canonical sequence from preflight through independent review and either a fully satisfied Conditional Autonomous Merge Gate or a human publication gate.

### Acceptance

1. Workflow order is Preflight -> Worktree -> Spec -> Plan -> Tasks -> Implement -> Validate -> Commit -> Independent Review -> Fix/Re-review -> Exact HEAD Gate -> Conditional Autonomous Merge Gate or Human Gate.
2. The default implementer/orchestrator and reviewer are distinct roles.
3. A change after reviewer approval invalidates the approval until the new exact HEAD is revalidated and reviewed.
4. Publication and destructive cleanup remain human-controlled unless the exact action is explicitly authorized or every Conditional Autonomous Merge Authority gate is satisfied.

## User Story 3 - Reuse ADOS across projects (P1)

A project can adopt ADOS without embedding AIverse-specific paths, commands, or product architecture into the core rules.

### Acceptance

1. Core documents are provider-neutral and project-neutral.
2. Project-specific paths, validation commands, allowed local artifacts, role adapters, and publication policy live in project configuration/integration documents.
3. AIverse is treated as an integration example, not as the ADOS core model.

## Functional Requirements

- FR-001: Define canonical human, implementer/orchestrator, and independent-reviewer responsibilities.
- FR-002: Define primary-repository protection and worktree-only feature-write rules.
- FR-003: Define a canonical feature lifecycle and stop conditions.
- FR-004: Define Exact HEAD Gate semantics.
- FR-005: Define publication/destructive-operation boundaries, including Conditional Autonomous Merge Authority.
- FR-006: Define narrow cleanup safety and prohibit broad destructive cleanup by default.
- FR-007: Define independent-review output expectations and bounded fix/re-review behavior at the governance level.
- FR-008: Provide a reusable project configuration template.
- FR-009: Provide an adoption path for AIverse without hard-coding AIverse into the core.
- FR-010: Keep repository foundation documentation-only; no executable automation engine is required in Spec 001.

## Non-Goals

- Executable CLI orchestration engine.
- Provider adapters for Codex, Claude, Gemini, or APIs.
- GitHub automation implementation.
- Automatic worktree creation code.
- Automatic validation runner.
- Automatic merge or cleanup outside the Conditional Autonomous Merge Authority.

## Success Criteria

- A new contributor can understand how to start a feature safely from the repository docs alone.
- The foundation explicitly prevents the primary-worktree contamination failure mode discovered during prior workflows.
- The core contains no required AIverse-specific absolute path.
- Spec 001 artifacts and governance docs agree on the workflow and human boundaries.

# Feature Specification: Worktree Lifecycle Engine

**Feature Branch**: `codex/003-worktree-lifecycle-engine`
**Status**: Draft
**Purpose**: Add an executable worktree lifecycle service that creates, verifies, and removes dedicated feature worktrees using explicit inputs and Execution Policy.

## User Story 1 - Create a feature worktree safely (P1)

An orchestrator can request a feature worktree only after the primary repository guardian passes.

### Acceptance

1. The request explicitly supplies primary repository path, worktree path, branch, and base ref.
2. The engine blocks when the primary repository guardian blocks.
3. The engine blocks when the requested worktree path equals the primary repository path.
4. The engine blocks when the requested worktree path already exists.
5. The engine creates the requested branch/worktree using explicit Git arguments.
6. The engine verifies the created worktree branch and repository root after creation.

## User Story 2 - Verify a feature worktree (P1)

An orchestrator can verify that a worktree exists, is registered, and is on the expected branch before any feature write.

### Acceptance

1. The engine detects missing worktrees.
2. The engine detects unregistered worktree paths.
3. The engine detects expected branch mismatch.
4. The engine reports deterministic violation codes and evidence.

## User Story 3 - Remove a merged feature worktree narrowly (P2)

An orchestrator can remove only an explicitly named worktree after policy permits autonomous cleanup.

### Acceptance

1. Removal requires `execution_policy.cleanup.autonomous = true`.
2. Removal blocks when the path is the primary repository path.
3. Removal blocks when the worktree is missing or unregistered.
4. Removal invokes only `git worktree remove <path>` for the explicit worktree path.
5. The engine does not delete branches, remotes, untracked files, or unrelated worktrees.

## Functional Requirements

- FR-001: Define immutable worktree lifecycle request and result records.
- FR-002: Implement deterministic lifecycle violation codes.
- FR-003: Implement a Git worktree provider for list/create/remove/branch/root operations.
- FR-004: Implement create flow gated by Primary Repository Guardian.
- FR-005: Implement verify flow for registered worktree and expected branch.
- FR-006: Implement remove flow gated by Execution Policy cleanup autonomy.
- FR-007: Add CLI commands for worktree verify/create/remove.
- FR-008: Add focused unit tests for safe create, verify, remove, and block paths.

## Non-Goals

- Remote branch deletion.
- Local branch deletion.
- Publication or PR management.
- Validation command execution.
- Recovery or repair behavior.
- Implicit branch or path naming.

## Success Criteria

- The engine can create and verify a worktree from explicit inputs.
- The engine refuses ambiguous or unsafe worktree operations.
- Tests prove no broad cleanup behavior is introduced.

# Feature Specification: Primary Repository Guardian + Execution Policy Foundation

**Feature Branch**: `codex/002-primary-repository-guardian`
**Status**: Draft
**Purpose**: Introduce ADOS' first executable, provider-neutral runtime model: an immutable Execution Policy and a read-only Primary Repository Guardian driven by that policy.

## User Story 1 - Load an explicit execution policy (P1)

An orchestrator can load one versioned policy document and receive either a validated immutable policy object or deterministic configuration errors.

### Acceptance

1. The policy document has a top-level `execution_policy` object.
2. Publication, review, cleanup, guardian, and validation sections are required.
3. Merge strategy is one of `merge`, `squash`, or `rebase`.
4. Reviewer, max review rounds, cleanup autonomy, guardian uncertainty behavior, and validation commands are validated without implicit defaults.
5. The validated model is immutable, serializable, provider-neutral, project-neutral, and versionable.

## User Story 2 - Audit primary repository safety (P1)

An orchestrator can run a read-only guardian against a repository and get deterministic pass/block evidence before feature work or publication.

### Acceptance

1. The guardian detects non-git directories.
2. The guardian detects repository path mismatch.
3. The guardian detects expected branch mismatch.
4. The guardian detects expected HEAD mismatch.
5. The guardian detects staged files.
6. The guardian detects dirty tracked files.
7. The guardian detects unexpected untracked files.
8. The guardian supports explicitly allowed local paths.
9. The guardian reports configuration errors deterministically.
10. The guardian never restores, resets, cleans, stashes, commits, pushes, deletes, or repairs.

## Functional Requirements

- FR-001: Define an immutable `ExecutionPolicy` runtime model.
- FR-002: Validate required policy fields without implicit defaults.
- FR-003: Serialize policy state for evidence and logs.
- FR-004: Define deterministic policy violation codes.
- FR-005: Define a read-only repository provider interface.
- FR-006: Implement a Git-backed read-only repository provider.
- FR-007: Implement Primary Repository Guardian checks for branch, HEAD, staged, tracked, untracked, repository mismatch, non-git directories, and configuration errors.
- FR-008: Support allowed local paths using explicit path patterns from the execution policy call site.
- FR-009: Provide a CLI entry point for policy validation and primary repository audit.
- FR-010: Add focused tests for policy validation and guardian behavior.

## Non-Goals

- Worktree lifecycle creation or deletion.
- Validation command execution engine.
- Claude review automation.
- Publication or merge automation.
- Recovery or repair behavior.
- Project-specific ADOS configuration files.

## Success Criteria

- A policy file can be validated from the CLI with deterministic output.
- A repository can be audited from the CLI without mutation.
- Tests prove the guardian blocks unsafe states and allows explicitly permitted local artifacts.
- No executable code hardcodes the ADOS repository name, path, branch, or GitHub repository.

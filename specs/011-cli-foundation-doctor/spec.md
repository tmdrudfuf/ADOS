# Feature Specification: ADOS CLI Foundation & Doctor

**Feature Branch**: `codex/011-cli-foundation-doctor`
**Status**: Draft
**Purpose**: Turn the existing ADOS engines into a cohesive user-facing CLI foundation and add `ados doctor` as the first production readiness diagnostic.

## User Story 1 - CLI root

An orchestrator can run `ados --help` and `ados --version` and receive stable, concise output without invoking workflow engines.

### Acceptance

1. The root CLI command is `ados`.
2. Existing engine subcommands remain available.
3. Future commands can be added without rewriting command parsing.

## User Story 2 - Doctor readiness

An orchestrator can run `ados doctor --project <path>` or `ados doctor <path>` and receive a read-only readiness report for an ADOS-managed development run.

### Acceptance

1. Doctor loads the canonical Project Configuration model.
2. Doctor invokes Primary Repository Guardian for repository safety.
3. Doctor checks execution policy, validation commands, review/implementer adapter availability, publication strategy, worktree, review rounds, and cleanup/archive configuration.
4. Doctor does not mutate the target repository or execute project validation/review/publication workloads.
5. Human output is concise and JSON output serializes the full typed result.

## Functional Requirements

- FR-001: Add a reusable CLI app/dispatcher boundary over existing commands.
- FR-002: Add typed DoctorResult and DoctorCheck records with deterministic ids and statuses.
- FR-003: Support `ados doctor --project <path>` and `ados doctor <path>`.
- FR-004: Support `--config <path>` and a minimal conventional project-local config discovery path.
- FR-005: Reuse Project Configuration, Execution Policy, and Primary Repository Guardian.
- FR-006: Detect configured executable availability using safe non-workload probes only.
- FR-007: Support `--json` output with no human prose in stdout.
- FR-008: Map READY/BLOCKED/INVALID to exit codes 0/1/2.
- FR-009: Keep doctor side-effect free.
- FR-010: Add focused tests, including Windows paths and an AIverse smoke fixture.

## Non-Goals

- Implementing `ados run`, `ados status`, `ados resume`, `ados review`, or `ados publish`.
- Running validation commands.
- Invoking Codex/Claude workloads.
- Creating specs, branches, worktrees, PRs, merges, deployments, or cleanup.

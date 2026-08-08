# Implementation Plan: ADOS Repository Foundation

## Summary

Create the durable documentation skeleton for ADOS as a reusable AI-development governance system. Spec 001 is intentionally documentation-only: it defines boundaries, roles, workflow order, guardian responsibilities, review semantics, project configuration, and bootstrap/adoption rules before executable automation is introduced.

## Architecture

ADOS is split into layers:

1. **Constitution** — invariant safety and authority rules.
2. **Workflow Engine specification** — canonical feature lifecycle and checkpoints.
3. **Guardian specifications** — independent checks for repository, worktree, SHA, review, publication, and cleanup state.
4. **Review policy** — independent reviewer contract and bounded fix/re-review loop.
5. **Recovery policy** — later specs will formalize failure recovery.
6. **Publication policy** — later specs will formalize push/PR/ready/merge/cleanup transitions.
7. **Project configuration** — project-specific paths, commands, role adapters, and allowed local state.

Spec 001 establishes the first four at governance level and supplies a configuration template. Executable tooling is deferred.

## Bootstrap Exception

An empty Git repository has no commit from which to create a feature branch/worktree. Therefore one minimal bootstrap commit on `main` is permitted solely to create repository history. Once that commit exists, all feature writes move to a dedicated branch/worktree. This is a one-time repository-initialization exception, not a feature-development precedent.

## Repository Structure

```text
README.md
AGENTS.md
CLAUDE.md
.gitignore

docs/
  constitution/core.md
  workflow/feature-lifecycle.md
  guardians/guardian-model.md
  review/independent-review.md
  templates/project-config.example.md

specs/
  001-repository-foundation/
    spec.md
    plan.md
    research.md
    data-model.md
    contracts/foundation-contract.md
    quickstart.md
    tasks.md
    checklists/requirements.md
```

## Design Decisions

### D1 — Core is project-neutral

ADOS core never requires an AIverse absolute path, JavaScript command, game-domain type, or GitHub repository name. Integrations supply these values.

### D2 — Primary checkout is a protected observation point

Feature writes are forbidden there. The workflow creates an isolated worktree first. This prevents pointer/spec drafts from leaking into the primary checkout.

### D3 — Review authority is SHA-bound

An approval is evidence for one exact candidate SHA only. Any subsequent commit makes it stale.

### D4 — Publication is distinct from implementation completion

Implementation/review completion does not imply authorization to push, mark ready, merge, or clean up.

### D5 — Safety stops are preferable to guesses

Unexpected dirty state, branch drift, ambiguous deletion scope, or SHA mismatch blocks progression until evidence resolves it.

## Validation

Because Spec 001 is documentation-only, validation consists of:

- cross-document consistency review
- path/link sanity
- no AIverse-specific absolute paths in core docs
- no contradictory human-boundary rules
- changed-file review against the bootstrap base

Executable markdown/link linting may be introduced later.

## Review

Independent Claude CLI review remains the intended bootstrap reviewer. If Claude cannot be invoked in the executing environment, that limitation must be reported explicitly; another model's self-review cannot be represented as Claude approval.

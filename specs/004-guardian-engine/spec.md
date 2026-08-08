# Spec 004 — Guardian Engine

## Goal

Define reusable precondition guardians that continuously protect repository location, worktree identity, candidate SHA integrity, validation evidence, independent review, publication boundaries, and cleanup/archive safety.

## Guardians

- PrimaryRepositoryGuardian
- WorktreeGuardian
- GitStateGuardian
- ValidationGuardian
- ReviewGuardian
- ShaGuardian
- PublicationGuardian
- CleanupGuardian

## Requirements

- Guardians evaluate before the mutation they protect.
- Guardian results are deterministic from supplied evidence.
- A failed required guardian blocks the transition and performs no guarded mutation.
- Guardians report a stable code, human-readable reason, and relevant evidence.
- Project configuration may add stricter guardians but cannot disable constitutional guards.
- PrimaryRepositoryGuardian distinguishes explicitly allowed untracked local artifacts from tracked mutations.
- WorktreeGuardian proves write location and feature branch identity.
- ShaGuardian proves reviewed SHA = validated SHA = current HEAD when approval is consumed.
- ValidationGuardian rejects stale validation evidence.
- ReviewGuardian requires the configured independent reviewer and exact reviewed candidate.
- PublicationGuardian requires the configured human authorization and unchanged remote/local candidate.
- CleanupGuardian requires merge/completion state plus artifact archive evidence before destructive cleanup.

## Non-goals

No process spawning, git mutation, GitHub mutation, or provider implementation in this Spec.
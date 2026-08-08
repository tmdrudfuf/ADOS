# ADOS Publication Engine

Publication is a sequence of separately guarded remote mutations.

## Push

Requires the configured publication authorization plus Exact HEAD success. Push only the intended feature branch and verify the remote head equals the approved candidate.

## Draft PR

Draft PR creation is a presentation/publication step, not review approval. Record repository, base, head branch, PR id, and remote head SHA. Keep the PR Draft unless Ready is separately authorized.

## Ready for Review

Ready authorization is distinct from push/Draft authorization. Recheck candidate integrity and any project-required checks before changing PR state.

## Merge

Merge authorization is a separate human decision. Recheck remote head, required review/CI state, base/comparison freshness, and expected PR identity immediately before merge.

## Forbidden implication

No stage implies permission for a later stage. In particular:

- Push does not imply Draft, Ready, Merge, or cleanup.
- Draft does not imply Ready or Merge.
- Ready does not imply Merge.
- Merge does not imply branch/worktree deletion.

Force push, auto-merge, branch deletion, deployment, and unrelated GitHub mutations require their own explicit policy/authorization.

## Evidence

For every remote mutation retain action, repository, target branch/PR, candidate SHA, authorization reference, timestamp, and remote result.
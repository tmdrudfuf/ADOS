# ADOS Guardian Engine

## Guardian result

Every guardian returns a conceptual result containing:

- guardian id
- status: Pass or Block
- stable reason code
- human-readable reason
- evidence snapshot or evidence references
- evaluated transition/action

A required guardian with status Block prevents the protected mutation.

## PrimaryRepositoryGuardian

Protects the authoritative primary checkout from feature writes. It accepts configured allowed untracked artifacts but blocks tracked modifications, staged changes, feature Spec directories, or implementation writes in the primary checkout.

## WorktreeGuardian

Verifies that the active write location is a registered feature worktree, the worktree branch matches the feature branch, and the primary checkout is not being used as the feature workspace.

## GitStateGuardian

Verifies expected base/head relationship, clean state requirements for the current transition, and absence of unapproved destructive/force operations.

## ValidationGuardian

Verifies all required commands passed and evidence still binds to the candidate being advanced. Any candidate change invalidates validation evidence.

## ReviewGuardian

Verifies reviewer identity, independence policy, decision, and exact reviewed candidate. It never converts internal/self-review into independent approval.

## ShaGuardian

Enforces the Exact HEAD invariant when approval is consumed:

`approvedReviewSha == validatedSha == currentHead`

A changed HEAD invalidates approval until validation and independent review are renewed.

## PublicationGuardian

Verifies explicit human authorization where configured, candidate integrity, target repository/branch, and that the requested remote mutation is within the authorized publication stage.

## CleanupGuardian

Verifies the feature reached the required completion/merge state, local review artifacts were archived when required, and the exact branch/worktree targeted for deletion is no longer needed.

## Composition

Transitions declare their required guardians. All required guardians must Pass. Project adapters may append stricter guards; they cannot remove constitutional guards.

## Failure policy

Guardians fail closed. A Block result must occur before the protected mutation. Recovery may gather new evidence and evaluate again, but must not relabel a failed guard as successful.
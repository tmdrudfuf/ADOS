# Data Model: ADOS Repository Foundation

Spec 001 defines conceptual governance records rather than executable runtime types.

## Project Configuration

- projectId
- primaryRepositoryPath
- defaultBranch
- allowedPrimaryUntrackedPaths[]
- validationCommands[]
- implementerAdapter
- reviewerAdapter
- publicationPolicy
- cleanupPolicy

## Feature Run

- featureId
- specNumber
- featureBranch
- worktreePath
- authoritativeBaseSha
- currentHeadSha
- phase
- validationSha
- approvedReviewSha
- publicationState

## Guardian Result

- guardianId
- featureId
- checkedAt
- status: Pass | Block
- reasonCode
- evidence[]

## Review Round

- featureId
- round
- reviewer
- reviewedSha
- decision: Approved | ChangesRequested | Unavailable
- blockingFindings[]
- nonBlockingFindings[]
- artifacts[]

## Publication Authorization

- featureId
- actor
- authorizedOperations[]
- authorizedSha
- grantedAt

Authorization is operation-specific and SHA-scoped where relevant. Approval of implementation is not implicitly authorization to merge or delete.

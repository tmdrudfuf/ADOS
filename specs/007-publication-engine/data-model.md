# Data Model: Publication Engine

## PublicationEvidence

- reviewDecision
- blockingFindings
- validationPassed
- approvedReviewSha
- validatedSha
- localHeadSha
- remoteBranchHeadSha
- prHeadSha
- exactHeadGate
- primaryRepositoryAudit
- featureWorktreeClean
- intendedBaseBranch
- intendedHeadBranch
- prBaseBranch
- prHeadBranch
- prMergeable
- unresolvedBlockingReviewState
- postApprovalCommit
- safetyRecoveryActive
- scopeApproved
- mergeStrategy
- forcePushRequired
- historyRewriteRequired
- bypassRequired

## PublicationGateResult

- status: PERMITTED | HUMAN_INTERVENTION_REQUIRED
- violations[]

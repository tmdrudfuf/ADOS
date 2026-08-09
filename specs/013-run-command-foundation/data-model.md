# Data Model: ADOS Run Command Foundation

## RunRequest

- projectPath
- configPath
- featureDescription
- specNumber
- dryRun

## RunEligibility

- status: `ELIGIBLE`, `BLOCKED`, `INVALID`
- violations
- warnings

## WorkflowRunRecord

- runId
- projectId
- specNumber
- featureSlug
- featureDescription
- authoritativeBaseSha
- primaryRepository
- featureBranch
- featureWorktree
- implementer
- reviewer
- executionPolicyVersion
- status
- nextStage

## RunResult

- status: `READY_FOR_IMPLEMENTATION`, `PLANNED`, `BLOCKED`, `INVALID`
- eligibility
- plan
- runRecord
- worktreeResult

# Data Model: Primary Repository Guardian + Execution Policy Foundation

## ExecutionPolicy

- schemaVersion
- publication
- review
- cleanup
- guardian
- validation

## PublicationPolicy

- mergeStrategy: merge | squash | rebase

## ReviewPolicy

- reviewer
- maxRounds

## CleanupPolicy

- autonomous

## GuardianPolicy

- stopOnUncertain

## ValidationPolicy

- commands[]

## GuardianCheck

- repositoryPath
- expectedRepositoryPath
- expectedBranch
- expectedHead
- allowedLocalPaths[]

## GuardianResult

- status: PASS | BLOCK
- violations[]
- evidence[]

## GuardianViolation

- code
- message
- evidence

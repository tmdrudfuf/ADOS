# Data Model: Validation Engine

## ValidationCommandResult

- command
- exitCode
- stdout
- stderr

## ValidationResult

- status: PASS | BLOCK
- headBefore
- headAfter
- commandResults[]
- violations[]

## ValidationViolation

- code
- message
- evidence

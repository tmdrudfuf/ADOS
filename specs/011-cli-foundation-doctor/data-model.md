# Data Model: ADOS Doctor

## DoctorResult

- status: `READY | BLOCKED | INVALID`
- checks: DoctorCheck[]

## DoctorCheck

- id
- status: `PASS | WARN | FAIL`
- summary
- blocking
- evidence
- violations

## DoctorViolation

- code
- message
- evidence

## ConfigDiscoveryResult

- status
- configPath
- violations

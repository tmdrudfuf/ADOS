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

## ProjectConfig roles extension

Spec011 uses the optional Project Configuration roles extension:

- roles.implementer?
- roles.reviewer?

The extension is project-neutral and backward compatible with Spec009 configs that omit roles.

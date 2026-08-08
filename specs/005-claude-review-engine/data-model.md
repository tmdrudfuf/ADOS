# Data Model: Claude Review Engine

## ReviewRequest

- candidateSha
- baseSha
- scope
- diff

## ReviewResult

- status: PASS | BLOCK
- decision: Approved | Changes Requested | Unavailable
- reviewedSha
- exitCode
- stdout
- stderr
- violations[]

## ReviewViolation

- code
- message
- evidence

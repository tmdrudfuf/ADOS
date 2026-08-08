# Data Model: Exact HEAD Gate

## ExactHeadGateResult

- status: MATCH | BLOCK
- approvedReviewSha
- validatedSha
- currentHeadSha
- violations[]

## ExactHeadGateViolation

- code
- message
- evidence

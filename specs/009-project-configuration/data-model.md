# Data Model: Project Configuration

## ProjectConfig

- projectId
- primaryRepositoryPath
- defaultBranch
- allowedPrimaryLocalPaths[]
- executionPolicy
- implementer?
- reviewer?

## Optional roles

- roles.implementer: optional configured implementer adapter command label/executable.
- roles.reviewer: optional configured reviewer adapter command label/executable. When omitted, callers may use `executionPolicy.review.reviewer` for reviewer-specific operations.

Invalid roles are deterministic configuration errors:

- PROJECT_CONFIG_INVALID_ROLES
- PROJECT_CONFIG_INVALID_IMPLEMENTER
- PROJECT_CONFIG_INVALID_REVIEWER

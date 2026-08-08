# Data Model: AIverse Integration

## AIverse Project Configuration

Uses the generic `ProjectConfig` model:

- `project.id`: `aiverse`
- `project.primary_repository_path`: explicit local path supplied by the adopting environment
- `project.default_branch`: explicit branch
- `project.allowed_primary_local_paths`: explicit local-only paths
- `execution_policy`: reusable policy object

No additional AIverse runtime model is introduced.

# Research: Primary Repository Guardian + Execution Policy Foundation

## Runtime format

JSON is used for executable policy fixtures and CLI input in Spec002 because Python can parse it with the standard library. The model remains serializable and versionable, and later specs may add YAML loading through an explicit dependency or parser.

## Git safety

The guardian uses only:

- `git rev-parse`
- `git status --porcelain`
- `git branch --show-current`

These commands observe repository state without mutating refs, index, working tree files, stashes, or remotes.

## Allowed local paths

Allowed local paths are explicit path patterns supplied to the guardian call. They are normalized to repository-relative POSIX-style paths before comparison. This keeps the execution policy provider-neutral while letting project configuration supply environment-specific local artifacts.

## Configuration errors

Configuration failures are reported as guardian blocks with deterministic codes rather than exceptions at the orchestration boundary. This lets later workflow engines stop safely with evidence.

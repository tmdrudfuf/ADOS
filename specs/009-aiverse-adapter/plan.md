# Plan — AIverse Project Adapter

Create a concrete adapter file under `adapters/aiverse/` and keep reusable ADOS policy free of AIverse-specific absolute paths and commands.

## Decisions

- YAML is the first portable adapter representation.
- Adapter data is declarative; it does not itself execute commands.
- Human-gate settings are explicit per publication/cleanup stage.
- Allowed primary untracked entries are explicit and narrow.
- Core Constitution remains authoritative over adapter configuration.

## Validation

Cross-check adapter values against the known AIverse workflow and verify all AIverse-specific paths/commands are isolated to the adapter/integration layer.
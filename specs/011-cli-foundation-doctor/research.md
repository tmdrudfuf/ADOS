# Research: ADOS CLI Foundation & Doctor

## CLI packaging

ADOS currently exposes `python -m ados` through `ados.__main__` and has no packaging metadata. Spec011 keeps module invocation as the stable development entrypoint and documents packaging as a later concern.

## Configuration

Spec009/010 define JSON Project Configuration as the canonical executable format. Doctor reuses `load_project_config` and adds only minimal discovery for conventional project-local JSON paths when `--config` is not supplied.

Spec011 adds optional `roles.implementer` and `roles.reviewer` fields to Project Configuration so Doctor can report adapter readiness without hardcoding ADOS, AIverse, Codex, or Claude. The fields are optional for backward compatibility; missing implementer adapters produce a non-blocking warning because earlier configs did not model an implementer role.

## Safety

Doctor is diagnostic-only. It may run safe version probes such as `git --version`, `codex --version`, and `claude --version`, but it must never execute configured project validation commands or reviewer workloads.

## Exit codes

Use the requested minimal contract: READY = 0, BLOCKED = 1, INVALID = 2.

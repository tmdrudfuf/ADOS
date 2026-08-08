# Research: Claude Review Engine

## Reviewer command

The engine uses the configured reviewer command as an executable shell command and passes the generated prompt on stdin. This keeps ADOS provider-neutral while supporting Claude CLI through policy.

## Decision parsing

Accepted decisions are:

- `Approved`
- `Changes Requested`

The parser tolerates Markdown emphasis and headings but does not infer approval from unrelated prose.

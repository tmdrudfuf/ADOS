# Review: Claude Review Engine

## Local validation status

Focused and full validation passed before candidate commit.

Claude independent review is pending.

## Claude review round 1

Decision: Changes Requested for `e3dbed1f0a09db6e29543fc53da8e4a0620d3af7`.

Blocking finding: markdown/whitespace decision parsing used an over-escaped whitespace regex and could reject valid emphasized decisions.

## Review scope

Claude must review exact validated SHA for:

- provider-neutral reviewer command use;
- exact SHA binding;
- deterministic decision parsing;
- unavailable reviewer handling;
- absence of fix, retry, publication, or exact-head-gate behavior.

# Research: Validation Engine

## Command execution

Execution Policy stores validation commands as strings. Spec004 runs those strings through the platform shell so existing project validation commands can be represented without inventing a command AST.

## SHA binding

The Git provider supplies HEAD before and after command execution. Any change means the validation result is stale for the starting candidate and must block.

# Data Model: Implementer Runtime

## ImplementerCommand

- adapter
- executable
- argv
- workingDirectory
- timeoutMs

## ImplementerRuntime

- runtimeId
- runId
- status
- command
- handoff

## ImplementerRuntimeResult

- runtimeId
- runId
- status
- exitCode
- timedOut
- stdout
- stderr
- headBefore
- headAfter
- changedFiles
- violations

## WorkflowRunRecord State

- `READY_FOR_IMPLEMENTATION`
- `READY_FOR_VALIDATION`
- `IMPLEMENTATION_FAILED`
- `IMPLEMENTATION_TIMED_OUT`
- `BLOCKED`

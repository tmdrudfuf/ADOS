# Research: ADOS Status Foundation

## Evidence

Status can safely use Project Configuration, read-only Git state, Primary Repository Guardian, `git worktree list`, Spec directories, and ignored `.agent-workflow/runs/*/ados-review-evidence.json` archive records.

## Non-inference

Spec directories prove only that a Spec directory exists. They do not prove merge, validation, or review state. Merge state is reported as evidence-backed only when an archive record contains a merge commit matching current repository evidence.

## Publication

Spec007 publication engine evaluates provided evidence but does not inspect GitHub. Spec012 therefore does not introduce a GitHub client. Publication state is `Merged` only when local archive evidence proves it; otherwise `Unavailable`.

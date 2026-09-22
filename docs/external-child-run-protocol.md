# External child-run protocol

ADOS exposes a versioned, machine-readable boundary for a trusted external development gateway. It preserves the existing `ados run` behavior and never accepts a caller-selected ADOS run ID.

## Prepare

```text
python -m ados external-run prepare --project <primary> --config <config> \
  --feature <description> --spec <number> --requirements-file <file> \
  --origin-file <json> --json
```

Prepare performs the normal project, primary-repository, assignment, branch, and worktree checks. It creates or idempotently resolves one run, persists `ados-run.json`, authoritative requirements, and `external-origin.json`, and returns the ADOS-generated `runId` and `originDigest`. It invokes no implementer or reviewer.

The schema-v1 origin object contains `originSystem`, `originSchemaVersion`, `projectId`, `backlogTaskId`, `developmentRequestId`, `preparationId`, `executionId`, `requirementsSha256`, `requestedFeature`, `purpose`, `recursionDepth`, `idempotencyKey`, and `createdAt`. Its digest is SHA-256 over canonical, sorted, compact UTF-8 JSON. The only schema-v1 purpose is `external-project-development`; recursion depth must be zero. The parent verification feature cannot be its own child.

Identical feature/base inputs plus the identical immutable origin resolve the same run. A different execution or idempotency identity is rejected, even if ordinary feature inputs collide. An external run cannot later be resumed through the ordinary feature heuristic without its origin binding.

## Continue exact run

```text
python -m ados external-run continue --project <primary> --config <config> \
  --run-id <ADOS-generated-id> --origin-digest <digest> --json
```

Continuation locates exactly one durable record by run ID, verifies project, primary repository, worktree, branch, base ancestry, authoritative requirements, origin artifact, and origin digest, then enters the existing pipeline. It never creates another run.

## Inspect exact run

```text
python -m ados external-run inspect --project <primary> --config <config> \
  --run-id <ADOS-generated-id> --origin-digest <digest> --json
```

Inspection is read-only. It invokes no agent, creates no recovery or authorization, and performs no publication. JSON reports durable identity, assignment and candidate owner, pipeline status, candidate/validation/review/HEAD identities, exact-HEAD state, blocking reasons, relevant artifact hashes, and technical readiness.

## Convergence and publication authority

After validation PASS, independent Approved review, and exact-HEAD MATCH, an external run writes versioned `technical-convergence.json`. `technicalPublicationReadiness=READY` additionally requires a clean worktree, independent reviewer, matching candidate/validated/reviewed/HEAD SHAs, valid origin binding, and no active recovery or review block.

Technical readiness is deliberately separate from remote mutation. External child runs report `remotePublicationState=NOT_REQUESTED` and `remotePublicationAuthority=HUMAN_REQUIRED`; the protocol does not push, create or ready a PR, merge, or deploy. Existing ordinary and conditional publication policy remains unchanged.

# ADOS Project Configuration Template

```json
{
  "project": {
    "id": "example-project",
    "primary_repository_path": "<absolute-or-resolved-local-path>",
    "default_branch": "main",
    "allowed_primary_local_paths": []
  },
  "execution_policy": {
    "schema_version": "1",
    "publication": {
      "merge_strategy": "<merge|squash|rebase>"
    },
    "review": {
      "reviewer": "<reviewer-command>",
      "max_rounds": 5
    },
    "cleanup": {
      "autonomous": true
    },
    "guardian": {
      "stop_on_uncertain": true
    },
    "validation": {
      "commands": [
        "<focused/full project command>"
      ]
    }
  }
}
```

Project configuration supplies environment-specific facts. It must not weaken core truthfulness, worktree-first, independent-review, or Exact HEAD rules without an explicit documented constitutional amendment.

## Adaptive implementer / reviewer role selection

`execution_policy.agent_roles` is optional. When it is absent, ADOS behaves exactly
as before: `roles.implementer` / `roles.reviewer` (or `review.reviewer`) are used
with no adaptive switching. When present it adds provider-neutral fixed or
adaptive role assignment.

Selection is deterministic and driven only by operator policy, an explicit
operator override, and verified runtime failure classification. ADOS never
invents token or usage percentages; when a runtime is unhealthy it records a
state such as `unavailable_quota`, `unavailable_auth`, `unavailable_capacity`,
`unavailable`, or `unknown`. Health probes are read-only: they only check that a
configured command is shell-safe and its executable resolves, and never execute
the configured command.

The chosen implementer and reviewer are persisted in the durable run record
under `agentAssignment` (ids, commands, mode, reason, sequence, candidate owner)
and are preserved across resume / recovery. Implementer and reviewer identities
must differ; if no independent reviewer can be selected, publication blocks.

### 1. Fixed Codex implementer / Claude reviewer

Preserves current behavior with no adaptive switching. Unexpected runtime
unavailability blocks using existing safe semantics.

```json
"agent_roles": {
  "mode": "fixed",
  "agents": {
    "codex": "codex exec --skip-git-repo-check",
    "claude": "claude --print"
  },
  "implementer_preference": ["codex"],
  "reviewer_preference": ["claude", "codex"]
}
```

### 2. Adaptive Codex-first mode

Codex is preferred for implementation and Claude for review. If Codex
implementation hits a classified external failure (quota / usage limit /
capacity / auth / provider runtime) at a safe boundary and the worktree is
clean with no candidate produced, ADOS fails over to Claude as implementer and
Codex becomes the independent reviewer. External failures are recorded as
`agentAvailabilityEvents` and do not consume the implementation-defect recovery
budget. Compilation failures, failing tests, and reviewer-requested changes are
implementation work and never trigger agent switching.

```json
"agent_roles": {
  "mode": "adaptive",
  "agents": {
    "codex": "codex exec --skip-git-repo-check",
    "claude": "claude --print"
  },
  "implementer_preference": ["codex", "claude"],
  "reviewer_preference": ["claude", "codex"]
}
```

### 3. Temporary Claude implementer / Codex reviewer mode

When the operator already knows Codex implementation budget is low, prefer
Claude as implementer before any failure occurs — without editing source or the
committed config. Use the adaptive policy above and pass the CLI override:

```
ados run --project <path> --feature "<desc>" --prefer-implementer claude
```

The override is explicit and is recorded in `agentAssignment.reason` in the
durable run record. The independent reviewer is then Codex, since the
implementer and reviewer must remain distinct.

# ADOS Canonical Workflow State Machine

## Transition discipline

A transition is allowed only when every guard for that transition passes before side effects begin. Failed guards produce a Blocked outcome and preserve the prior durable state.

## Canonical path

`Preflight → WorktreeReady → SpecReady → ImplementationActive → ValidationPassed → ReviewPending`

Review then branches:

- `ReviewPending → ChangesRequested → ImplementationActive`
- `ReviewPending → ReviewApproved → ExactHeadVerified`

Publication then proceeds only through configured human gates:

`ExactHeadVerified → PublicationAuthorized → DraftPublished → ReadyAuthorized → MergeAuthorized → Merged → CleanupAuthorized → Archived → Cleaned`

## Required guards

### Preflight → WorktreeReady

- authoritative repository and base resolved
- primary checkout has no unexpected tracked mutation
- feature branch/worktree identity is unique
- no feature writes occurred in primary checkout

### WorktreeReady → SpecReady

- current write location is the feature worktree
- branch identity matches the feature
- Spec, Plan, and Tasks are coherent

### ImplementationActive → ValidationPassed

- required validation commands completed successfully
- validation evidence binds to current candidate SHA/content

### ValidationPassed → ReviewPending

- candidate is committed or otherwise immutably identified
- independent reviewer identity is distinct from implementer when required

### ReviewPending → ReviewApproved

- reviewer decision is Approved
- decision binds to exact candidate SHA

### ReviewApproved → ExactHeadVerified

- approved review SHA = validated SHA = current feature HEAD
- no relevant tracked changes exist after approval

### ExactHeadVerified → PublicationAuthorized

- explicit human authorization exists when publication is human-only

### DraftPublished → ReadyAuthorized

- explicit human authorization exists
- remote head still equals approved candidate

### ReadyAuthorized → MergeAuthorized

- explicit human merge decision exists
- required checks remain satisfied

### Merged → CleanupAuthorized

- explicit human cleanup authorization exists when configured
- required local review/workflow artifacts are archived first

## Blocked state

Blocked is a truthful workflow outcome, not permission to skip a gate. Recovery must re-evaluate the failed guard and resume from the last valid durable state.

## Evidence

Each significant transition should retain: feature identity, actor, timestamp, source/target state, relevant SHA(s), guard result, reason, and external mutation flags where applicable.
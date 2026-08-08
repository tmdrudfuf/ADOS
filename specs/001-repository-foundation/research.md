# Research: ADOS Repository Foundation

## Decision: Use a dedicated repository

ADOS is a reusable development-governance system, not an AIverse product feature. Keeping it independent prevents project-specific architecture from contaminating the core and allows reuse across repositories.

## Decision: Protect the primary checkout

Feature work must happen only after a dedicated branch/worktree exists and is verified. This directly addresses the failure mode where Spec files and pointer updates were created in the primary checkout before isolation.

## Decision: Permit one empty-repository bootstrap commit

A truly empty repository has no commit from which a feature branch can be created. One minimal bootstrap commit on `main` is allowed. No Spec or feature implementation is part of that exception.

## Decision: Keep Spec 001 documentation-only

Building executable automation before governance is stable would encode assumptions prematurely. Spec 001 defines contracts and boundaries; later specs can implement guardians and workflow engines against those contracts.

## Decision: Keep independent review truthful

If the configured independent reviewer cannot be executed, the workflow must report `Independent review unavailable` rather than substituting implementer self-review and calling it approval.

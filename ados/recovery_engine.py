"""Advisory recovery engine."""

from __future__ import annotations

from dataclasses import asdict, dataclass


@dataclass(frozen=True)
class RecoveryIssue:
    source: str
    code: str
    message: str
    evidence: dict[str, str]


@dataclass(frozen=True)
class RecoveryDecision:
    status: str
    action: str
    issues: tuple[RecoveryIssue, ...]

    def to_dict(self) -> dict[str, object]:
        return {
            "status": self.status,
            "action": self.action,
            "issues": [asdict(issue) for issue in self.issues],
        }


class RecoveryEngine:
    def classify(self, issues: tuple[RecoveryIssue, ...]) -> RecoveryDecision:
        if not issues:
            return RecoveryDecision("RECOVERABLE", "continue_workflow", ())

        codes = {issue.code for issue in issues}
        if codes & {"DIRTY_TRACKED_FILES", "STAGED_FILES", "UNEXPECTED_UNTRACKED_FILES", "PRIMARY_REPOSITORY_NOT_SAFE"}:
            return RecoveryDecision("HUMAN_INTERVENTION_REQUIRED", "inspect_primary_repository_state", issues)
        if "VALIDATION_COMMAND_FAILED" in codes:
            return RecoveryDecision("RECOVERABLE", "fix_validation_failures_then_revalidate", issues)
        if "CHANGES_REQUESTED" in codes:
            return RecoveryDecision("RECOVERABLE", "fix_valid_blocking_findings_then_revalidate", issues)
        if codes & {"APPROVED_REVIEW_SHA_MISMATCH", "VALIDATED_SHA_MISMATCH", "SHA_MISMATCH"}:
            return RecoveryDecision("RECOVERABLE", "repeat_validation_and_independent_review", issues)
        if codes & {"PR_NOT_MERGEABLE", "MERGE_CONFLICT", "MERGE_STRATEGY_MISMATCH"}:
            return RecoveryDecision("HUMAN_INTERVENTION_REQUIRED", "resolve_publication_blocker", issues)
        return RecoveryDecision("HUMAN_INTERVENTION_REQUIRED", "unknown_recovery_condition", issues)

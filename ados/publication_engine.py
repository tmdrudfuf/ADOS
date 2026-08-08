"""Autonomous publication gate evaluator."""

from __future__ import annotations

from dataclasses import asdict, dataclass

from .execution_policy import ExecutionPolicy


@dataclass(frozen=True)
class PublicationEvidence:
    review_decision: str
    blocking_findings: tuple[str, ...]
    validation_passed: bool
    approved_review_sha: str
    validated_sha: str
    local_head_sha: str
    remote_branch_head_sha: str
    pr_head_sha: str
    exact_head_gate: str
    primary_repository_audit: str
    feature_worktree_clean: bool
    intended_base_branch: str
    intended_head_branch: str
    pr_base_branch: str
    pr_head_branch: str
    pr_mergeable: bool
    unresolved_blocking_review_state: bool
    post_approval_commit: bool
    safety_recovery_active: bool
    scope_approved: bool
    merge_strategy: str
    force_push_required: bool
    history_rewrite_required: bool
    bypass_required: bool


@dataclass(frozen=True)
class PublicationViolation:
    code: str
    message: str
    evidence: dict[str, str]


@dataclass(frozen=True)
class PublicationGateResult:
    status: str
    violations: tuple[PublicationViolation, ...]

    def to_dict(self) -> dict[str, object]:
        return {
            "status": self.status,
            "violations": [asdict(violation) for violation in self.violations],
        }


class PublicationEngine:
    def evaluate(self, *, policy: ExecutionPolicy, evidence: PublicationEvidence) -> PublicationGateResult:
        violations: list[PublicationViolation] = []
        _require(violations, evidence.review_decision == "Approved", "REVIEW_NOT_APPROVED", "review decision is not Approved", {"decision": evidence.review_decision})
        _require(violations, not evidence.blocking_findings, "BLOCKING_FINDINGS_PRESENT", "blocking findings are present", {"count": str(len(evidence.blocking_findings))})
        _require(violations, evidence.validation_passed, "VALIDATION_NOT_PASSED", "validation did not pass", {})
        shas = {
            evidence.approved_review_sha,
            evidence.validated_sha,
            evidence.local_head_sha,
            evidence.remote_branch_head_sha,
            evidence.pr_head_sha,
        }
        _require(violations, len(shas) == 1 and "" not in shas, "SHA_MISMATCH", "publication SHAs do not all match", {"sha_count": str(len(shas))})
        _require(violations, evidence.exact_head_gate == "MATCH", "EXACT_HEAD_GATE_NOT_MATCH", "Exact HEAD Gate is not MATCH", {"exact_head_gate": evidence.exact_head_gate})
        _require(violations, evidence.primary_repository_audit == "SAFE", "PRIMARY_REPOSITORY_NOT_SAFE", "primary repository audit is not SAFE", {"audit": evidence.primary_repository_audit})
        _require(violations, evidence.feature_worktree_clean, "FEATURE_WORKTREE_DIRTY", "feature worktree is not clean", {})
        _require(violations, evidence.pr_base_branch == evidence.intended_base_branch, "PR_BASE_MISMATCH", "PR base branch does not match intended branch", {"expected": evidence.intended_base_branch, "actual": evidence.pr_base_branch})
        _require(violations, evidence.pr_head_branch == evidence.intended_head_branch, "PR_HEAD_MISMATCH", "PR head branch does not match intended branch", {"expected": evidence.intended_head_branch, "actual": evidence.pr_head_branch})
        _require(violations, evidence.pr_mergeable, "PR_NOT_MERGEABLE", "PR is not mergeable", {})
        _require(violations, not evidence.unresolved_blocking_review_state, "UNRESOLVED_BLOCKING_REVIEW_STATE", "unresolved blocking review state exists", {})
        _require(violations, not evidence.post_approval_commit, "POST_APPROVAL_COMMIT", "a commit appeared after approval", {})
        _require(violations, not evidence.safety_recovery_active, "SAFETY_RECOVERY_ACTIVE", "safety or recovery condition is active", {})
        _require(violations, evidence.scope_approved, "SCOPE_NOT_APPROVED", "scope is not approved", {})
        _require(violations, evidence.merge_strategy == policy.publication.merge_strategy, "MERGE_STRATEGY_MISMATCH", "merge strategy does not match Execution Policy", {"expected": policy.publication.merge_strategy, "actual": evidence.merge_strategy})
        _require(violations, not evidence.force_push_required, "FORCE_PUSH_REQUIRED", "force push is required", {})
        _require(violations, not evidence.history_rewrite_required, "HISTORY_REWRITE_REQUIRED", "history rewrite is required", {})
        _require(violations, not evidence.bypass_required, "BYPASS_REQUIRED", "auto-merge bypass or merge bypass is required", {})
        return PublicationGateResult("HUMAN_INTERVENTION_REQUIRED" if violations else "PERMITTED", tuple(violations))


def _require(
    violations: list[PublicationViolation],
    condition: bool,
    code: str,
    message: str,
    evidence: dict[str, str],
) -> None:
    if not condition:
        violations.append(PublicationViolation(code, message, evidence))

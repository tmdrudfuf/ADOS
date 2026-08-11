"""Read-only ADOS project status application service."""

from __future__ import annotations

from dataclasses import asdict, dataclass
import json
from pathlib import Path
import re
from typing import Any

from .doctor import DoctorRequest, DoctorService, discover_project_config
from .git_provider import GitRepositoryProvider, MergedPullRequest
from .primary_repository_guardian import PrimaryRepositoryGuardian
from .project_config import ProjectConfig, ProjectConfigError, load_project_config
from .repository_provider import RepositoryProviderError
from .run_pipeline import transient_review_blocked_evidence
from .worktree_classification import branch_spec_number, classify_worktree
from .worktree_provider import GitWorktreeProvider, WorktreeRecord


@dataclass(frozen=True)
class StatusRequest:
    project_path: Path
    config_path: Path | None = None


@dataclass(frozen=True)
class StatusSection:
    state: str
    evidence: dict[str, str]
    reason_codes: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, object]:
        return {"state": self.state, "evidence": dict(self.evidence), "reason_codes": list(self.reason_codes)}


@dataclass(frozen=True)
class WorktreeStatus:
    path: str
    branch: str
    head: str
    primary: bool
    state: str
    classification: str = "UNKNOWN"
    reason_codes: tuple[str, ...] = ()
    evidence: dict[str, str] | None = None

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass(frozen=True)
class NextAction:
    action: str
    reason: str
    evidence: dict[str, str]

    def to_dict(self) -> dict[str, str | dict[str, str]]:
        return {"action": self.action, "reason": self.reason, "evidence": dict(self.evidence)}


@dataclass(frozen=True)
class StatusResult:
    status: str
    project: StatusSection
    repository: StatusSection
    guardian: StatusSection
    workflow: StatusSection
    spec: StatusSection
    worktrees: tuple[WorktreeStatus, ...]
    validation: StatusSection
    review: StatusSection
    exact_head_gate: StatusSection
    publication: StatusSection
    recovery: StatusSection
    next_action: NextAction

    def to_dict(self) -> dict[str, object]:
        return {
            "status": self.status,
            "project": self.project.to_dict(),
            "repository": self.repository.to_dict(),
            "guardian": self.guardian.to_dict(),
            "workflow": self.workflow.to_dict(),
            "spec": self.spec.to_dict(),
            "worktrees": [worktree.to_dict() for worktree in self.worktrees],
            "validation": self.validation.to_dict(),
            "review": self.review.to_dict(),
            "exactHeadGate": self.exact_head_gate.to_dict(),
            "publication": self.publication.to_dict(),
            "recovery": self.recovery.to_dict(),
            "nextAction": self.next_action.to_dict(),
        }


class StatusService:
    def __init__(
        self,
        *,
        doctor: DoctorService | None = None,
        guardian: PrimaryRepositoryGuardian | None = None,
        git: GitRepositoryProvider | None = None,
        worktrees: GitWorktreeProvider | None = None,
    ) -> None:
        self.doctor = doctor or DoctorService()
        self.guardian = guardian or PrimaryRepositoryGuardian()
        self.git = git or GitRepositoryProvider()
        self.worktree_provider = worktrees or GitWorktreeProvider()

    def run(self, request: StatusRequest) -> StatusResult:
        project_path = request.project_path.resolve()
        doctor_result = self.doctor.run(DoctorRequest(project_path=project_path, config_path=request.config_path))
        config_path = request.config_path.resolve() if request.config_path else discover_project_config(project_path)
        config: ProjectConfig | None = None
        project_section = StatusSection("Unknown", {"project_path": str(project_path)})

        if not project_path.exists() or not project_path.is_dir():
            return _invalid_result(project_section, "PROJECT_PATH_INVALID", {"path": str(project_path)})

        if config_path is None:
            return _invalid_result(project_section, "PROJECT_CONFIG_NOT_FOUND", {"project_path": str(project_path)})

        try:
            config = load_project_config(config_path)
        except ProjectConfigError as exc:
            return _invalid_result(project_section, exc.code, {"config_path": str(config_path)})

        project_section = StatusSection(
            "Configured",
            {"project_id": config.project_id, "project_path": str(project_path), "config_path": str(config_path)},
        )

        repository = self._repository(project_path)
        guardian = self._guardian(project_path, config)
        current_head = repository.evidence.get("head", "")
        merged_pull_requests = self.git.merged_pull_requests(project_path, current_head)
        evidence = _latest_complete_evidence(project_path, current_head, self.git, merged_pull_requests)
        merged_archive = _merged_archive_evidence(project_path, current_head, self.git, merged_pull_requests)
        latest_merged_spec = _archive_spec_number(merged_archive)
        merged_spec_commits = _merged_spec_commits(project_path, current_head, self.git, merged_pull_requests)
        merged_pull_request_heads = tuple(record.head_ref_oid for record in merged_pull_requests)
        worktrees = self._worktrees(project_path, current_head, latest_merged_spec, merged_spec_commits, merged_pull_request_heads)
        spec = _spec_status(project_path, worktrees, current_head, self.git, merged_pull_requests)
        validation = _validation_status(evidence, current_head)
        review = _review_status(evidence, current_head)
        exact_head = _exact_head_status(evidence, current_head)
        publication = _publication_status(evidence, current_head)
        workflow = _workflow_status(worktrees, spec, publication, doctor_result.status)
        recovery = _recovery_status(guardian, validation, review, exact_head, workflow)
        next_action = _next_action(guardian, workflow, validation, review, exact_head, spec, recovery)
        status = _overall_status(guardian, workflow, recovery)

        return StatusResult(
            status=status,
            project=project_section,
            repository=repository,
            guardian=guardian,
            workflow=workflow,
            spec=spec,
            worktrees=worktrees,
            validation=validation,
            review=review,
            exact_head_gate=exact_head,
            publication=publication,
            recovery=recovery,
            next_action=next_action,
        )

    def _repository(self, project_path: Path) -> StatusSection:
        try:
            root = self.git.repository_root(project_path)
            branch = self.git.current_branch(project_path)
            head = self.git.current_head(project_path)
        except RepositoryProviderError as exc:
            return StatusSection("Invalid", {"path": str(project_path), "message": exc.message}, (exc.code,))
        return StatusSection("Ready", {"root": str(root), "branch": branch, "head": head})

    def _guardian(self, project_path: Path, config: ProjectConfig) -> StatusSection:
        result = self.guardian.audit(
            policy=config.execution_policy,
            repository_path=project_path,
            expected_repository_path=config.primary_repository_path,
            expected_branch=config.default_branch,
            allowed_local_paths=config.allowed_primary_local_paths,
        )
        if result.status == "PASS":
            return StatusSection("SAFE", {"status": result.status})
        evidence: dict[str, str] = {"status": result.status}
        for violation in result.violations:
            evidence.update({f"{violation.code}.{key}": value for key, value in violation.evidence.items()})
        return StatusSection("BLOCKED", evidence, tuple(violation.code for violation in result.violations))

    def _worktrees(
        self,
        project_path: Path,
        current_head: str,
        latest_merged_spec: int | None,
        merged_spec_commits: dict[int, tuple[str, ...]],
        merged_pull_request_heads: tuple[str, ...],
    ) -> tuple[WorktreeStatus, ...]:
        try:
            root = self.git.repository_root(project_path)
            records = self.worktree_provider.list_worktrees(project_path)
        except RepositoryProviderError as exc:
            return (WorktreeStatus(str(project_path), "", "", True, "Unavailable", "UNKNOWN", (exc.code,), {"error": exc.message}),)

        statuses: list[WorktreeStatus] = []
        for record in records:
            state, reasons = self._worktree_state(record)
            classification = classify_worktree(
                record=record,
                primary_root=root,
                current_main_head=current_head,
                latest_merged_spec=latest_merged_spec,
                merged_spec_commits=merged_spec_commits,
                merged_pull_request_heads=merged_pull_request_heads,
                git=self.git,
            )
            statuses.append(
                WorktreeStatus(
                    path=str(record.path),
                    branch=record.branch,
                    head=record.head,
                    primary=record.path == root,
                    state=state,
                    classification=classification.classification,
                    reason_codes=tuple(dict.fromkeys((*reasons, *classification.reason_codes))),
                    evidence=classification.evidence,
                )
            )
        return tuple(statuses)

    def _worktree_state(self, record: WorktreeRecord) -> tuple[str, tuple[str, ...]]:
        try:
            status = self.git.status(record.path)
        except RepositoryProviderError as exc:
            return "Unavailable", (exc.code,)
        reasons: list[str] = []
        if status.staged:
            reasons.append("STAGED_FILES")
        if status.dirty_tracked:
            reasons.append("DIRTY_TRACKED_FILES")
        if status.untracked:
            reasons.append("UNTRACKED_FILES")
        return ("Dirty" if reasons else "Clean"), tuple(reasons)


def _invalid_result(project: StatusSection, code: str, evidence: dict[str, str]) -> StatusResult:
    failed = StatusSection("Invalid", evidence, (code,))
    return StatusResult(
        status="INVALID",
        project=project,
        repository=failed,
        guardian=StatusSection("Unavailable", {}, (code,)),
        workflow=StatusSection("Unknown", {}),
        spec=StatusSection("Unknown", {}),
        worktrees=(),
        validation=StatusSection("Unavailable", {}),
        review=StatusSection("Unavailable", {}),
        exact_head_gate=StatusSection("Unavailable", {}),
        publication=StatusSection("Unknown", {}),
        recovery=StatusSection("HUMAN_INTERVENTION_REQUIRED", evidence, (code,)),
        next_action=NextAction("HUMAN_INTERVENTION_REQUIRED", code, evidence),
    )


def _spec_status(
    project_path: Path,
    worktrees: tuple[WorktreeStatus, ...],
    current_head: str,
    git: GitRepositoryProvider,
    merged_pull_requests: tuple[MergedPullRequest, ...] = (),
) -> StatusSection:
    numbers = sorted(_spec_number(path.name) for path in (project_path / "specs").glob("*") if path.is_dir())
    present = [number for number in numbers if number is not None]
    next_unused = _next_unused(present)
    active_numbers = sorted(
        number
        for worktree in worktrees
        if not worktree.primary and worktree.classification == "ACTIVE"
        for number in [branch_spec_number(worktree.branch)]
        if number is not None
    )
    historical_numbers = sorted(
        number
        for worktree in worktrees
        if not worktree.primary and worktree.classification == "MERGED_HISTORICAL"
        for number in [branch_spec_number(worktree.branch)]
        if number is not None
    )
    evidence = {
        "spec_directories": ",".join(f"{number:03d}" for number in present),
        "latest_merged_spec": "Unknown",
        "latest_merged_spec_basis": "Unavailable",
        "active_spec": ",".join(f"{number:03d}" for number in active_numbers) if active_numbers else "None",
        "historical_spec": ",".join(f"{number:03d}" for number in historical_numbers) if historical_numbers else "None",
        "next_unused_spec": f"{next_unused:03d}",
    }
    archive = _merged_archive_evidence(project_path, current_head, git, merged_pull_requests)
    archive_spec = _archive_spec_number(archive)
    if archive_spec is not None:
        evidence["latest_merged_spec"] = f"{archive_spec:03d}"
        evidence["latest_merged_spec_basis"] = "merge_commit"
    return StatusSection("Resolved", evidence)


def _workflow_status(worktrees: tuple[WorktreeStatus, ...], spec: StatusSection, publication: StatusSection, doctor_status: str) -> StatusSection:
    active = [worktree for worktree in worktrees if not worktree.primary and worktree.classification == "ACTIVE"]
    historical = [worktree for worktree in worktrees if not worktree.primary and worktree.classification == "MERGED_HISTORICAL"]
    preserved = [worktree for worktree in worktrees if not worktree.primary and worktree.classification == "PRESERVED"]
    unknown = [worktree for worktree in worktrees if not worktree.primary and worktree.classification == "UNKNOWN"]
    run_evidence = _active_run_evidence(active)
    base_evidence = {
        "active_worktree_count": str(len(active)),
        "historical_worktree_count": str(len(historical)),
        "preserved_worktree_count": str(len(preserved)),
        "unknown_worktree_count": str(len(unknown)),
        "doctor_status": doctor_status,
    }
    if unknown:
        evidence = dict(base_evidence)
        evidence["unknown_worktrees"] = ",".join(worktree.path for worktree in unknown)
        evidence.update(run_evidence)
        return StatusSection("Blocked", evidence, ("UNKNOWN_WORKTREE_CLASSIFICATION",))
    if preserved:
        evidence = dict(base_evidence)
        evidence["preserved_worktrees"] = ",".join(worktree.path for worktree in preserved)
        evidence.update(run_evidence)
        return StatusSection("Blocked", evidence, ("PRESERVED_WORKTREE_REQUIRES_HUMAN",))
    if len(active) > 1:
        evidence = dict(base_evidence)
        evidence.update(run_evidence)
        return StatusSection("Active", evidence, ("MULTIPLE_ACTIVE_WORKTREES",))
    if len(active) == 1:
        evidence = dict(base_evidence)
        evidence.update({"active_worktree": active[0].path, "branch": active[0].branch})
        evidence.update(run_evidence)
        return StatusSection("Active", evidence)
    if publication.state == "Merged":
        evidence = dict(base_evidence)
        evidence["latest_merged_spec"] = spec.evidence.get("latest_merged_spec", "Unknown")
        return StatusSection("Idle", evidence)
    return StatusSection("Idle", base_evidence)


def _active_run_evidence(active: list[WorktreeStatus]) -> dict[str, str]:
    for worktree in active:
        runs = Path(worktree.path) / ".agent-workflow" / "runs"
        if not runs.is_dir():
            continue
        for candidate in sorted(runs.glob("*/ados-run.json"), key=lambda path: str(path)):
            try:
                raw = json.loads(candidate.read_text(encoding="utf-8-sig"))
            except (OSError, json.JSONDecodeError):
                continue
            if isinstance(raw, dict) and raw.get("status"):
                resumable = _active_run_resumable(candidate, str(raw.get("status", "")))
                return {
                    "run_id": str(raw.get("runId", "")),
                    "run_status": str(raw.get("status", "")),
                    "run_spec": str(raw.get("specNumber", "")),
                    "run_record": str(candidate),
                    "resumable": str(resumable),
                }
    return {}


def _active_run_resumable(record_path: Path, status: str) -> bool:
    resumable_statuses = {
        "READY_FOR_IMPLEMENTATION",
        "IMPLEMENTATION_FAILED",
        "IMPLEMENTATION_TIMED_OUT",
        "READY_FOR_VALIDATION",
        "VALIDATION_FAILED",
        "READY_FOR_REVIEW",
        "REVIEWING",
        "REVIEW_CHANGES_REQUESTED",
        "REVIEW_APPROVED",
        "READY_FOR_PUBLICATION",
        "PR_CREATED",
        "PR_READY",
        "MERGED",
        "CLEANUP_INCOMPLETE",
    }
    if status in resumable_statuses:
        return True
    if status != "REVIEW_BLOCKED":
        return False
    return not transient_review_blocked_evidence(
        _read_json_object(record_path.with_name("candidate.json")),
        _read_json_object(record_path.with_name("validation-runtime.json")),
        _read_json_object(record_path.with_name("review-runtime.json")),
    )


def _read_json_object(path: Path) -> dict[str, Any] | None:
    try:
        raw = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return None
    return raw if isinstance(raw, dict) else None


def _validation_status(evidence: dict[str, Any] | None, current_head: str) -> StatusSection:
    if not evidence or not evidence.get("validated_sha"):
        return StatusSection("Unavailable", {})
    validated = str(evidence.get("validated_sha", ""))
    if _is_merged_candidate_evidence(evidence, current_head) and evidence.get("validation_status", "PASS") == "PASS":
        return StatusSection(
            "PassedForMergedCandidate",
            {"validated_sha": validated, "current_head": current_head, "merge_commit": str(evidence.get("merge_commit", ""))},
        )
    if current_head and validated != current_head:
        return StatusSection("Stale", {"validated_sha": validated, "current_head": current_head}, ("VALIDATION_STALE",))
    return StatusSection("Passed", {"validated_sha": validated})


def _review_status(evidence: dict[str, Any] | None, current_head: str) -> StatusSection:
    if not evidence or not evidence.get("approved_review_sha"):
        return StatusSection("Unavailable", {})
    reviewed = str(evidence.get("approved_review_sha", ""))
    decision = str(evidence.get("claude_decision", "Unknown"))
    if _is_merged_candidate_evidence(evidence, current_head) and decision == "Approved":
        return StatusSection(
            "ApprovedForMergedCandidate",
            {"reviewed_sha": reviewed, "current_head": current_head, "decision": decision, "merge_commit": str(evidence.get("merge_commit", ""))},
        )
    if current_head and reviewed != current_head:
        return StatusSection("Stale", {"reviewed_sha": reviewed, "current_head": current_head, "decision": decision}, ("REVIEW_STALE",))
    if decision == "Approved":
        return StatusSection("Approved", {"reviewed_sha": reviewed, "decision": decision})
    if decision == "Changes Requested":
        return StatusSection("ChangesRequested", {"reviewed_sha": reviewed, "decision": decision}, ("CHANGES_REQUESTED",))
    return StatusSection("Unavailable", {"reviewed_sha": reviewed, "decision": decision})


def _exact_head_status(evidence: dict[str, Any] | None, current_head: str) -> StatusSection:
    if not evidence:
        return StatusSection("Unavailable", {})
    approved = str(evidence.get("approved_review_sha", ""))
    validated = str(evidence.get("validated_sha", ""))
    merge_commit = str(evidence.get("merge_commit", ""))
    if not approved or not validated or not current_head:
        return StatusSection("Unavailable", {"approved_review_sha": approved, "validated_sha": validated, "current_head": current_head})
    if _is_merged_candidate_evidence(evidence, current_head) and approved == validated and approved != current_head:
        return StatusSection(
            "Merged",
            {
                "approved_review_sha": approved,
                "validated_sha": validated,
                "current_head": current_head,
                "merge_commit": merge_commit,
                "reason": "feature gate satisfied before merge",
            },
        )
    if approved == validated == current_head:
        return StatusSection("MATCH", {"approved_review_sha": approved, "validated_sha": validated, "current_head": current_head})
    return StatusSection("MISMATCH", {"approved_review_sha": approved, "validated_sha": validated, "current_head": current_head}, ("SHA_MISMATCH",))


def _publication_status(evidence: dict[str, Any] | None, current_head: str) -> StatusSection:
    if not evidence:
        return StatusSection("Unavailable", {})
    merge_commit = str(evidence.get("merge_commit", ""))
    if evidence.get("_merged_into_current_head") == "true" or (merge_commit and current_head and merge_commit == current_head):
        return StatusSection("Merged", {"merge_commit": merge_commit, "pull_request": str(evidence.get("pull_request", ""))})
    if merge_commit:
        return StatusSection("Stale", {"merge_commit": merge_commit, "current_head": current_head}, ("PUBLICATION_STALE",))
    return StatusSection("Unavailable", {})


def _recovery_status(
    guardian: StatusSection,
    validation: StatusSection,
    review: StatusSection,
    exact_head: StatusSection,
    workflow: StatusSection,
) -> StatusSection:
    reasons: list[str] = []
    for section in (guardian, validation, review, exact_head, workflow):
        reasons.extend(section.reason_codes)
    blocking = {
        "SHA_MISMATCH",
        "MULTIPLE_ACTIVE_WORKTREES",
        "UNKNOWN_WORKTREE_CLASSIFICATION",
        "PRESERVED_WORKTREE_REQUIRES_HUMAN",
    }
    if guardian.state == "BLOCKED" or any(reason in blocking for reason in reasons):
        return StatusSection("HUMAN_INTERVENTION_REQUIRED", {}, tuple(reasons))
    return StatusSection("NotRequired", {}, tuple(reasons))


def _next_action(
    guardian: StatusSection,
    workflow: StatusSection,
    validation: StatusSection,
    review: StatusSection,
    exact_head: StatusSection,
    spec: StatusSection,
    recovery: StatusSection,
) -> NextAction:
    if recovery.state == "HUMAN_INTERVENTION_REQUIRED":
        return NextAction("HUMAN_INTERVENTION_REQUIRED", "blocked_or_ambiguous_state", {"reason_codes": ",".join(recovery.reason_codes)})
    if guardian.state != "SAFE":
        return NextAction("Resolve dirty primary", "primary_repository_not_safe", {})
    if review.state == "ChangesRequested":
        return NextAction("Address review findings", "review_changes_requested", {})
    if validation.state == "Stale" or review.state == "Stale" or exact_head.state == "MISMATCH":
        return NextAction("Revalidate current HEAD", "stale_sha_bound_evidence", {})
    if workflow.state == "Active":
        return NextAction("Inspect active worktree", "feature_worktree_active", workflow.evidence)
    return NextAction(f"Start Spec {spec.evidence.get('next_unused_spec', 'Unknown')}", "idle_ready_for_next_spec", {})


def _overall_status(guardian: StatusSection, workflow: StatusSection, recovery: StatusSection) -> str:
    if recovery.state == "HUMAN_INTERVENTION_REQUIRED" or guardian.state == "BLOCKED":
        return "BLOCKED"
    if workflow.state == "Active":
        return "ACTIVE"
    return "IDLE"


def _latest_complete_evidence(
    project_path: Path,
    current_head: str,
    git: GitRepositoryProvider,
    merged_pull_requests: tuple[MergedPullRequest, ...] = (),
) -> dict[str, Any] | None:
    merged = _merged_archive_evidence(project_path, current_head, git, merged_pull_requests)
    if merged is not None:
        return merged
    candidates = _archive_evidence_records(project_path)
    candidates.sort(key=lambda path: path.stat().st_mtime, reverse=True)
    for candidate in candidates:
        raw = _read_archive(candidate)
        if raw is not None:
            return raw
    return None


def _merged_archive_evidence(
    project_path: Path,
    current_head: str,
    git: GitRepositoryProvider,
    merged_pull_requests: tuple[MergedPullRequest, ...] = (),
) -> dict[str, Any] | None:
    if not current_head:
        return None
    merged: list[tuple[int, str, dict[str, Any]]] = []
    for pull_request in merged_pull_requests:
        evidence = _merged_pull_request_evidence(project_path, current_head, git, pull_request)
        spec_number = _archive_spec_number(evidence)
        if evidence is not None and spec_number is not None:
            merged.append((spec_number, str(evidence["merge_commit"]), evidence))
    for candidate in sorted(_archive_evidence_records(project_path), key=lambda path: str(path)):
        raw = _read_archive(candidate)
        if raw is None:
            continue
        raw = _normalize_archive_evidence(raw)
        spec_number = _archive_spec_number(raw)
        merge_commit = str(raw.get("merge_commit", ""))
        if spec_number is None or not merge_commit:
            continue
        try:
            is_merged = git.is_ancestor(project_path, merge_commit, current_head)
            candidate_reachable = _candidate_reaches_current(project_path, current_head, git, raw)
        except RepositoryProviderError:
            continue
        if is_merged and candidate_reachable:
            raw["_merged_into_current_head"] = "true"
            merged.append((spec_number, merge_commit, raw))
    if not merged:
        return None
    return max(merged, key=lambda item: (item[0], item[1]))[2]


def _merged_spec_commits(
    project_path: Path,
    current_head: str,
    git: GitRepositoryProvider,
    merged_pull_requests: tuple[MergedPullRequest, ...] = (),
) -> dict[int, tuple[str, ...]]:
    if not current_head:
        return {}
    merged: dict[int, list[str]] = {}
    for pull_request in merged_pull_requests:
        evidence = _merged_pull_request_evidence(project_path, current_head, git, pull_request)
        spec_number = _archive_spec_number(evidence)
        if evidence is not None and spec_number is not None:
            merged.setdefault(spec_number, []).append(str(evidence["merge_commit"]))
    for candidate in sorted(_archive_evidence_records(project_path), key=lambda path: str(path)):
        raw = _read_archive(candidate)
        if raw is None:
            continue
        raw = _normalize_archive_evidence(raw)
        spec_number = _archive_spec_number(raw)
        merge_commit = str(raw.get("merge_commit", ""))
        if spec_number is None or not merge_commit:
            continue
        try:
            is_merged = git.is_ancestor(project_path, merge_commit, current_head)
            candidate_reachable = _candidate_reaches_current(project_path, current_head, git, raw)
        except RepositoryProviderError:
            continue
        if is_merged and candidate_reachable:
            merged.setdefault(spec_number, []).append(merge_commit)
    return {spec: tuple(commits) for spec, commits in merged.items()}


def _archive_evidence_records(project_path: Path) -> list[Path]:
    runs = project_path / ".agent-workflow" / "runs"
    if not runs.is_dir():
        return []
    return list(runs.glob("*/ados-review-evidence.json"))


def _read_archive(candidate: Path) -> dict[str, Any] | None:
    try:
        raw = json.loads(candidate.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return None
    if isinstance(raw, dict):
        raw["_evidence_path"] = str(candidate)
        return _normalize_archive_evidence(raw)
    return None


def _normalize_archive_evidence(raw: dict[str, Any]) -> dict[str, Any]:
    normalized = dict(raw)
    pr = normalized.get("pr")
    if isinstance(pr, dict):
        if not normalized.get("pull_request") and pr.get("number"):
            normalized["pull_request"] = pr.get("number")
        if not normalized.get("merge_commit") and pr.get("merge_commit_sha"):
            normalized["merge_commit"] = pr.get("merge_commit_sha")
        if not normalized.get("candidate_sha") and pr.get("head_sha"):
            normalized["candidate_sha"] = pr.get("head_sha")
    if not normalized.get("candidate_sha"):
        candidate = normalized.get("approved_review_sha") or normalized.get("validated_sha")
        if candidate:
            normalized["candidate_sha"] = candidate
    validation = normalized.get("validation")
    if isinstance(validation, dict) and validation.get("status"):
        normalized["validation_status"] = validation.get("status")
    return normalized


def _merged_pull_request_evidence(
    project_path: Path,
    current_head: str,
    git: GitRepositoryProvider,
    pull_request: MergedPullRequest,
) -> dict[str, Any] | None:
    spec = _pull_request_spec(pull_request)
    if spec is None:
        return None
    if not pull_request.merge_commit_oid or not pull_request.head_ref_oid:
        return None
    try:
        if not git.is_ancestor(project_path, pull_request.merge_commit_oid, current_head):
            return None
        if not git.is_ancestor(project_path, pull_request.head_ref_oid, current_head):
            return None
    except RepositoryProviderError:
        return None
    return {
        "spec": f"{spec:03d}-{_pull_request_slug(pull_request)}",
        "validated_sha": pull_request.head_ref_oid,
        "approved_review_sha": pull_request.head_ref_oid,
        "candidate_sha": pull_request.head_ref_oid,
        "claude_decision": "Approved",
        "validation_status": "PASS",
        "pull_request": pull_request.number,
        "merge_commit": pull_request.merge_commit_oid,
        "_evidence_source": "merged_pull_request",
        "_merged_into_current_head": "true",
    }


def _pull_request_spec(pull_request: MergedPullRequest) -> int | None:
    for value in (pull_request.head_ref_name, pull_request.title):
        match = re.search(r"(?:^|/|\b)(\d{3})(?:-|:|\b)", value)
        if match:
            return int(match.group(1))
    return None


def _pull_request_slug(pull_request: MergedPullRequest) -> str:
    match = re.search(r"\d{3}[-: ]+(.+)$", pull_request.head_ref_name)
    if not match:
        match = re.search(r"\d{3}[-: ]+(.+)$", pull_request.title)
    if not match:
        return "merged-pull-request"
    words = re.findall(r"[a-z0-9]+", match.group(1).lower())
    return "-".join(words[:8]) or "merged-pull-request"


def _is_merged_candidate_evidence(evidence: dict[str, Any], current_head: str) -> bool:
    merge_commit = str(evidence.get("merge_commit", ""))
    candidate = str(evidence.get("candidate_sha", "") or evidence.get("approved_review_sha", "") or evidence.get("validated_sha", ""))
    return bool(merge_commit and current_head and candidate and evidence.get("_merged_into_current_head") == "true")


def _candidate_reaches_current(project_path: Path, current_head: str, git: GitRepositoryProvider, evidence: dict[str, Any]) -> bool:
    candidate = str(evidence.get("candidate_sha", "") or evidence.get("approved_review_sha", "") or evidence.get("validated_sha", ""))
    if not candidate:
        return True
    try:
        return git.is_ancestor(project_path, candidate, current_head)
    except RepositoryProviderError:
        return False


def _spec_number(name: str) -> int | None:
    match = re.match(r"^(\d{3})-", name)
    return int(match.group(1)) if match else None


def _archive_spec_number(evidence: dict[str, Any] | None) -> int | None:
    if not evidence:
        return None
    value = str(evidence.get("spec", ""))
    match = re.search(r"(\d{3})", value)
    return int(match.group(1)) if match else None


def _next_unused(numbers: list[int]) -> int:
    current = 1
    for number in sorted(set(numbers)):
        if number == current:
            current += 1
    return current

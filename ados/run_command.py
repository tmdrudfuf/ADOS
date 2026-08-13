"""ADOS run startup application service."""

from __future__ import annotations

from dataclasses import asdict, dataclass
import hashlib
import json
from pathlib import Path
import re
from typing import Any

from .doctor import DoctorRequest, DoctorService, discover_project_config
from .git_provider import GitRepositoryProvider
from .implementer_runtime import ImplementerRuntime, ImplementerRuntimeOutcome, SAFE_TIMEOUT_MS
from .primary_repository_guardian import PrimaryRepositoryGuardian
from .project_config import ProjectConfig, ProjectConfigError, load_project_config
from .repository_provider import RepositoryProviderError
from .run_pipeline import PIPELINE_READY_STATUSES, PipelineOutcome, RunPipeline, transient_review_blocked_evidence, validation_failed_evidence
from .status import StatusRequest, StatusService
from .worktree_lifecycle import WorktreeLifecycleEngine, WorktreeRequest, WorktreeLifecycleResult
from .worktree_provider import GitWorktreeProvider


RESUMABLE_RUN_STATUSES = {"READY_FOR_IMPLEMENTATION", "IMPLEMENTATION_FAILED", "IMPLEMENTATION_TIMED_OUT", *PIPELINE_READY_STATUSES}


@dataclass(frozen=True)
class RunRequest:
    project_path: Path
    feature_description: str
    spec_number: int | None = None
    config_path: Path | None = None
    dry_run: bool = False
    implementer_timeout_ms: int = SAFE_TIMEOUT_MS


@dataclass(frozen=True)
class RunViolation:
    code: str
    message: str
    evidence: dict[str, str]

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass(frozen=True)
class RunEligibility:
    status: str
    violations: tuple[RunViolation, ...]
    warnings: tuple[RunViolation, ...] = ()

    def to_dict(self) -> dict[str, object]:
        return {
            "status": self.status,
            "violations": [violation.to_dict() for violation in self.violations],
            "warnings": [warning.to_dict() for warning in self.warnings],
        }


@dataclass(frozen=True)
class RunPlan:
    project_path: str
    spec_number: str
    feature_slug: str
    feature_branch: str
    feature_worktree: str
    authoritative_base_sha: str

    def to_dict(self) -> dict[str, str]:
        return asdict(self)


@dataclass(frozen=True)
class WorkflowRunRecord:
    run_id: str
    project_id: str
    spec_number: str
    feature_slug: str
    feature_description: str
    authoritative_base_sha: str
    primary_repository: str
    feature_branch: str
    feature_worktree: str
    implementer: str
    reviewer: str
    execution_policy_version: str
    status: str
    next_stage: str

    def to_dict(self) -> dict[str, str]:
        return {
            "runId": self.run_id,
            "projectId": self.project_id,
            "specNumber": self.spec_number,
            "featureSlug": self.feature_slug,
            "featureDescription": self.feature_description,
            "authoritativeBaseSha": self.authoritative_base_sha,
            "primaryRepository": self.primary_repository,
            "featureBranch": self.feature_branch,
            "featureWorktree": self.feature_worktree,
            "implementer": self.implementer,
            "reviewer": self.reviewer,
            "executionPolicyVersion": self.execution_policy_version,
            "status": self.status,
            "nextStage": self.next_stage,
        }


@dataclass(frozen=True)
class RunResult:
    status: str
    eligibility: RunEligibility
    plan: RunPlan | None = None
    run_record: WorkflowRunRecord | None = None
    worktree_result: WorktreeLifecycleResult | None = None
    implementer_result: ImplementerRuntimeOutcome | None = None
    pipeline_result: PipelineOutcome | None = None
    resumed: bool = False

    def to_dict(self) -> dict[str, object]:
        return {
            "status": self.status,
            "eligibility": self.eligibility.to_dict(),
            "plan": self.plan.to_dict() if self.plan else None,
            "runRecord": self.run_record.to_dict() if self.run_record else None,
            "worktreeResult": self.worktree_result.to_dict() if self.worktree_result else None,
            "implementerResult": self.implementer_result.to_dict() if self.implementer_result else None,
            "pipelineResult": self.pipeline_result.to_dict() if self.pipeline_result else None,
            "resumed": self.resumed,
        }


class RunService:
    def __init__(
        self,
        *,
        doctor: DoctorService | None = None,
        status: StatusService | None = None,
        guardian: PrimaryRepositoryGuardian | None = None,
        git: GitRepositoryProvider | None = None,
        worktrees: GitWorktreeProvider | None = None,
        lifecycle: WorktreeLifecycleEngine | None = None,
        implementer: ImplementerRuntime | None = None,
        pipeline: RunPipeline | None = None,
    ) -> None:
        self.doctor = doctor or DoctorService()
        self.status = status or StatusService()
        self.guardian = guardian or PrimaryRepositoryGuardian()
        self.git = git or GitRepositoryProvider()
        self.worktrees = worktrees or GitWorktreeProvider()
        self.lifecycle = lifecycle or WorktreeLifecycleEngine()
        self.implementer = implementer or ImplementerRuntime()
        self.pipeline = pipeline or RunPipeline(implementer=self.implementer, lifecycle=self.lifecycle)

    def run(self, request: RunRequest) -> RunResult:
        if not request.feature_description.strip():
            return _invalid("FEATURE_DESCRIPTION_MISSING", "feature description is required", {})

        project_path = request.project_path.resolve()
        config_path = request.config_path.resolve() if request.config_path else discover_project_config(project_path)
        if config_path is None:
            return _invalid("PROJECT_CONFIG_NOT_FOUND", "project configuration was not found", {"project_path": str(project_path)})
        try:
            config = load_project_config(config_path)
        except ProjectConfigError as exc:
            return _invalid(exc.code, exc.message, {"config_path": str(config_path)})

        try:
            plan = self._plan(project_path, config, request)
        except _RunPlanError as exc:
            eligibility = RunEligibility("BLOCKED", (RunViolation(exc.code, exc.message, exc.evidence),))
            return RunResult("BLOCKED", eligibility)

        resume = self._resumable_run(project_path, config, plan)
        eligibility = self._eligibility(project_path, config, config_path, plan, resume)
        if eligibility.status != "ELIGIBLE":
            return RunResult("INVALID" if eligibility.status == "INVALID" else "BLOCKED", eligibility, plan)
        if request.dry_run:
            return RunResult("PLANNED", eligibility, plan, resume.record if resume else self._record(config, request, plan), resumed=resume is not None)

        if resume is not None:
            pipeline_result = self.pipeline.run(config=config, run_record_path=resume.record_path, timeout_ms=request.implementer_timeout_ms)
            updated_record = _record_from_mapping(pipeline_result.run_record) if pipeline_result.run_record else resume.record
            return RunResult(pipeline_result.status, eligibility, plan, updated_record, implementer_result=pipeline_result.implementer_result, pipeline_result=pipeline_result, resumed=True)

        worktree_request = WorktreeRequest(
            primary_repository_path=project_path,
            worktree_path=Path(plan.feature_worktree),
            branch=plan.feature_branch,
            base_ref=plan.authoritative_base_sha,
            expected_primary_branch=config.default_branch,
            expected_primary_head=plan.authoritative_base_sha,
            allowed_primary_local_paths=config.allowed_primary_local_paths,
        )
        created = self.lifecycle.create(policy=config.execution_policy, request=worktree_request)
        if created.status != "PASS":
            eligibility = RunEligibility("BLOCKED", tuple(RunViolation(item.code, item.message, item.evidence) for item in created.violations))
            return RunResult("BLOCKED", eligibility, plan, worktree_result=created)

        record = self._record(config, request, plan)
        record_path = self._record_path(Path(plan.feature_worktree), plan.spec_number, plan.feature_slug)
        record_path.parent.mkdir(parents=True, exist_ok=True)
        record_path.write_text(json.dumps(record.to_dict(), indent=2, sort_keys=True), encoding="utf-8")
        pipeline_result = self.pipeline.run(config=config, run_record_path=record_path, timeout_ms=request.implementer_timeout_ms)
        updated_record = _record_from_mapping(pipeline_result.run_record) if pipeline_result.run_record else record
        return RunResult(pipeline_result.status, eligibility, plan, updated_record, created, implementer_result=pipeline_result.implementer_result, pipeline_result=pipeline_result)

    def _plan(self, project_path: Path, config: ProjectConfig, request: RunRequest) -> RunPlan:
        try:
            branch = self.git.current_branch(project_path)
            head = self.git.current_head(project_path)
            upstream = self.git.ref_head(project_path, f"origin/{config.default_branch}")
        except RepositoryProviderError as exc:
            raise _RunPlanError(exc.code, exc.message, {"project_path": str(project_path)}) from exc
        if branch != config.default_branch:
            raise _RunPlanError("DEFAULT_BRANCH_MISMATCH", "run must start from configured default branch", {"expected": config.default_branch, "actual": branch})
        if head != upstream:
            raise _RunPlanError("DEFAULT_BRANCH_NOT_SYNCED", "local default branch does not match origin", {"local_head": head, "origin_head": upstream})

        slug = _slug(request.feature_description)
        spec_number = request.spec_number
        if spec_number is None:
            spec_number = self._existing_resumable_spec(project_path, config, slug, head)
        if spec_number is None:
            spec_number = _next_unused_spec(project_path, self.git)
        spec = f"{spec_number:03d}"
        branch_name = f"codex/{spec}-{slug}"
        worktree_path = project_path.parent / f"{project_path.name}-{slug}"
        return RunPlan(str(project_path), spec, slug, branch_name, str(worktree_path), head)

    def _existing_resumable_spec(self, project_path: Path, config: ProjectConfig, slug: str, base_head: str) -> int | None:
        matches: set[int] = set()
        run_dirs = [project_path / ".agent-workflow" / "runs"]
        run_dirs.extend(worktree.path / ".agent-workflow" / "runs" for worktree in self.worktrees.list_worktrees(project_path))
        for runs in run_dirs:
            for record_path in sorted(runs.glob("*/ados-run.json"), key=lambda path: str(path)):
                raw = _read_mapping(record_path)
                if raw is None:
                    continue
                try:
                    record = _record_from_mapping(raw)
                except (KeyError, TypeError):
                    continue
                if (
                    record.status in RESUMABLE_RUN_STATUSES
                    and (record.status != "REVIEW_BLOCKED" or _review_blocked_is_resumable(record_path))
                    and record.project_id == config.project_id
                    and Path(record.primary_repository).resolve() == project_path
                    and record.feature_slug == slug
                    and (record.authoritative_base_sha == base_head or record.status in {"MERGED", "CLEANUP_INCOMPLETE"})
                    and record.execution_policy_version == config.execution_policy.schema_version
                ):
                    matches.add(int(record.spec_number))
        if len(matches) > 1:
            raise _RunPlanError("AMBIGUOUS_RESUMABLE_RUN", "multiple resumable runs match this feature", {"feature_slug": slug})
        return next(iter(matches)) if matches else None

    def _eligibility(self, project_path: Path, config: ProjectConfig, config_path: Path, plan: RunPlan, resume: "_ResumeCandidate | None" = None) -> RunEligibility:
        violations: list[RunViolation] = []
        warnings: list[RunViolation] = []
        doctor = self.doctor.run(DoctorRequest(project_path=project_path, config_path=config_path))
        if doctor.status == "INVALID":
            return RunEligibility("INVALID", (_violation("DOCTOR_INVALID", "Doctor reported invalid project state", {"status": doctor.status}),))
        for check in doctor.checks:
            if check.status == "FAIL" and check.blocking:
                for violation in check.violations:
                    violations.append(RunViolation(violation.code, violation.message, violation.evidence))

        status = self.status.run(StatusRequest(project_path=project_path, config_path=config_path))
        warnings.extend(_historical_warnings(status))
        historical_count = status.workflow.evidence.get("historical_worktree_count", "0")
        if historical_count not in {"", "0"}:
            warnings.append(
                _violation(
                    "HISTORICAL_WORKTREES_PRESENT",
                    "historical merged worktrees are present but do not block a new run",
                    {"historical_worktree_count": historical_count},
                )
            )
        active_count = status.workflow.evidence.get("active_worktree_count", "0")
        if active_count not in {"", "0"} and resume is None:
            violations.append(
                _violation(
                    "ACTIVE_WORKTREE_PRESENT",
                    "active feature worktree prevents starting a new run",
                    {"active_worktree_count": active_count},
                )
            )
        blocking_recovery = _run_blocking_recovery_codes(status.recovery.state, status.recovery.reason_codes)
        if blocking_recovery:
            violations.append(
                _violation(
                    "UNSAFE_RECOVERY_STATE",
                    "Status recovery requires human intervention before starting a new run",
                    {"reason_codes": ",".join(blocking_recovery), "status": status.status},
                )
            )

        guardian = self.guardian.audit(
            policy=config.execution_policy,
            repository_path=project_path,
            expected_repository_path=config.primary_repository_path,
            expected_branch=config.default_branch,
            expected_head=plan.authoritative_base_sha,
            allowed_local_paths=config.allowed_primary_local_paths,
        )
        for violation in guardian.violations:
            violations.append(RunViolation(violation.code, violation.message, violation.evidence))

        spec_number = int(plan.spec_number)
        if (project_path / "specs" / f"{plan.spec_number}-{plan.feature_slug}").exists() and resume is None:
            violations.append(_violation("SPEC_DIRECTORY_EXISTS", "requested Spec directory already exists", {"spec": plan.spec_number}))
        if any(_spec_number(path.name) == spec_number for path in (project_path / "specs").glob("*") if path.is_dir()) and resume is None:
            violations.append(_violation("SPEC_NUMBER_USED", "requested Spec number already has a directory", {"spec": plan.spec_number}))
        try:
            if self.git.branch_exists(project_path, plan.feature_branch) and resume is None:
                violations.append(_violation("FEATURE_BRANCH_EXISTS", "feature branch already exists", {"branch": plan.feature_branch}))
        except RepositoryProviderError as exc:
            violations.append(_violation(exc.code, exc.message, {"branch": plan.feature_branch}))
        if Path(plan.feature_worktree).exists() and resume is None:
            violations.append(_violation("WORKTREE_PATH_EXISTS", "feature worktree path already exists", {"worktree_path": plan.feature_worktree}))
        for record in self.worktrees.list_worktrees(project_path):
            if resume is not None and record.path == Path(resume.record.feature_worktree).resolve():
                continue
            if record.branch == plan.feature_branch or _branch_spec_number(record.branch) == spec_number:
                violations.append(_violation("CONFLICTING_WORKTREE", "active worktree already owns requested Spec or branch", {"branch": record.branch, "path": str(record.path)}))

        return RunEligibility("BLOCKED" if violations else "ELIGIBLE", tuple(violations), tuple(warnings))

    def _resumable_run(self, project_path: Path, config: ProjectConfig, plan: RunPlan) -> "_ResumeCandidate | None":
        candidates: list[_ResumeCandidate] = []
        primary_runs = project_path / ".agent-workflow" / "runs"
        candidates.extend(self._resume_candidates_in_run_dir(config, plan, None, primary_runs))
        for record in self.worktrees.list_worktrees(project_path):
            runs = record.path / ".agent-workflow" / "runs"
            candidates.extend(self._resume_candidates_in_run_dir(config, plan, record, runs))
        deduped: dict[tuple[str, str], _ResumeCandidate] = {}
        for candidate in candidates:
            deduped.setdefault((candidate.record.run_id, str(Path(candidate.record.feature_worktree).resolve())), candidate)
        candidates = list(deduped.values())
        if len(candidates) == 1:
            return candidates[0]
        return None

    def _resume_candidates_in_run_dir(self, config: ProjectConfig, plan: RunPlan, worktree_record: Any | None, runs: Path) -> list["_ResumeCandidate"]:
        candidates: list[_ResumeCandidate] = []
        if not runs.is_dir():
            return candidates
        for candidate_path in sorted(runs.glob("*/ados-run.json"), key=lambda path: str(path)):
            raw = _read_mapping(candidate_path)
            if raw is None:
                continue
            candidate = self._resume_candidate(config, plan, worktree_record, candidate_path, raw)
            if candidate is not None:
                candidates.append(candidate)
        return candidates

    def _resume_candidate(
        self,
        config: ProjectConfig,
        plan: RunPlan,
        worktree_record: Any | None,
        record_path: Path,
        raw: dict[str, Any],
    ) -> "_ResumeCandidate | None":
        try:
            record = _record_from_mapping(raw)
        except (KeyError, TypeError):
            return None
        expected = self._record_from_plan(config, plan, record.feature_description)
        if record.status not in RESUMABLE_RUN_STATUSES:
            return None
        if record.status == "REVIEW_BLOCKED" and not _review_blocked_is_resumable(record_path):
            return None
        if record.status == "VALIDATION_FAILED" and validation_failed_evidence(
            _read_mapping(record_path.with_name("candidate.json")),
            _read_mapping(record_path.with_name("validation-runtime.json")),
        ):
            return None
        cleanup_resume = record.status in {"MERGED", "CLEANUP_INCOMPLETE", "NO_CHANGES_CLEANUP_INCOMPLETE"}
        if (
            (record.run_id != expected.run_id and not cleanup_resume)
            or record.project_id != expected.project_id
            or Path(record.primary_repository).resolve() != Path(expected.primary_repository).resolve()
            or record.spec_number != expected.spec_number
            or record.feature_slug != expected.feature_slug
            or (record.authoritative_base_sha != expected.authoritative_base_sha and not cleanup_resume)
            or record.feature_branch != expected.feature_branch
            or Path(record.feature_worktree).resolve() != Path(expected.feature_worktree).resolve()
            or record.execution_policy_version != expected.execution_policy_version
            or record.implementer != expected.implementer
            or record.reviewer != expected.reviewer
            or (worktree_record is not None and worktree_record.branch != expected.feature_branch)
            or (worktree_record is not None and worktree_record.path != Path(expected.feature_worktree).resolve())
        ):
            return None
        return _ResumeCandidate(record_path=record_path, record=record)

    def _record(self, config: ProjectConfig, request: RunRequest, plan: RunPlan) -> WorkflowRunRecord:
        return self._record_from_plan(config, plan, request.feature_description)

    def _record_from_plan(self, config: ProjectConfig, plan: RunPlan, feature_description: str) -> WorkflowRunRecord:
        run_id = _run_id(config.project_id, plan.spec_number, plan.feature_slug, plan.authoritative_base_sha)
        return WorkflowRunRecord(
            run_id=run_id,
            project_id=config.project_id,
            spec_number=plan.spec_number,
            feature_slug=plan.feature_slug,
            feature_description=feature_description,
            authoritative_base_sha=plan.authoritative_base_sha,
            primary_repository=plan.project_path,
            feature_branch=plan.feature_branch,
            feature_worktree=plan.feature_worktree,
            implementer=config.implementer or "Unavailable",
            reviewer=config.reviewer or config.execution_policy.review.reviewer,
            execution_policy_version=config.execution_policy.schema_version,
            status="READY_FOR_IMPLEMENTATION",
            next_stage="implementation_handoff",
        )

    def _record_path(self, worktree: Path, spec: str, slug: str) -> Path:
        return worktree / ".agent-workflow" / "runs" / f"{spec}-{slug}" / "ados-run.json"


def _invalid(code: str, message: str, evidence: dict[str, str]) -> RunResult:
    return RunResult("INVALID", RunEligibility("INVALID", (RunViolation(code, message, evidence),)))


def _record_from_mapping(raw: dict[str, Any]) -> WorkflowRunRecord:
    return WorkflowRunRecord(
        run_id=str(raw["runId"]),
        project_id=str(raw["projectId"]),
        spec_number=str(raw["specNumber"]),
        feature_slug=str(raw["featureSlug"]),
        feature_description=str(raw["featureDescription"]),
        authoritative_base_sha=str(raw["authoritativeBaseSha"]),
        primary_repository=str(raw["primaryRepository"]),
        feature_branch=str(raw["featureBranch"]),
        feature_worktree=str(raw["featureWorktree"]),
        implementer=str(raw["implementer"]),
        reviewer=str(raw["reviewer"]),
        execution_policy_version=str(raw["executionPolicyVersion"]),
        status=str(raw["status"]),
        next_stage=str(raw["nextStage"]),
    )


def _read_mapping(path: Path) -> dict[str, Any] | None:
    try:
        raw = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return None
    return raw if isinstance(raw, dict) else None


def _review_blocked_is_resumable(record_path: Path) -> bool:
    record = _read_mapping(record_path)
    candidate = _read_mapping(record_path.with_name("candidate.json"))
    validation = _read_mapping(record_path.with_name("validation-runtime.json"))
    if _legacy_no_change_shape(record, candidate, validation):
        return True
    return not transient_review_blocked_evidence(
        candidate,
        validation,
        _read_mapping(record_path.with_name("review-runtime.json")),
    )


def _legacy_no_change_shape(record: dict[str, Any] | None, candidate: dict[str, Any] | None, validation: dict[str, Any] | None) -> bool:
    if not isinstance(record, dict) or str(record.get("status", "")) != "REVIEW_BLOCKED":
        return False
    if not isinstance(candidate, dict):
        return False
    candidate_sha = str(candidate.get("candidate_sha") or candidate.get("candidateSha") or "")
    base_sha = str(record.get("authoritativeBaseSha", ""))
    if not candidate_sha or candidate_sha != base_sha:
        return False
    if isinstance(validation, dict):
        return (
            str(validation.get("head_before", "")) == candidate_sha
            and str(validation.get("head_after", "")) == candidate_sha
        )
    return True


def _violation(code: str, message: str, evidence: dict[str, str]) -> RunViolation:
    return RunViolation(code, message, evidence)


def _historical_warnings(status: Any) -> list[RunViolation]:
    warnings: list[RunViolation] = []
    for section_name in ("validation", "review", "publication"):
        section = getattr(status, section_name)
        if section.state == "Stale":
            warnings.append(_violation(f"HISTORICAL_{section_name.upper()}_STALE", "historical evidence is stale for current HEAD", section.evidence))
    return warnings


def _run_blocking_recovery_codes(recovery_state: str, reason_codes: tuple[str, ...]) -> tuple[str, ...]:
    if recovery_state != "HUMAN_INTERVENTION_REQUIRED":
        return ()
    historical = {"VALIDATION_STALE", "REVIEW_STALE", "PUBLICATION_STALE", "SHA_MISMATCH"}
    return tuple(code for code in reason_codes if code not in historical)


def _slug(description: str) -> str:
    words = re.findall(r"[a-z0-9]+", description.lower())
    return "-".join(words[:6]) or "feature"


def _next_unused_spec(project_path: Path, git: GitRepositoryProvider) -> int:
    used: set[int] = set()
    specs = project_path / "specs"
    if specs.is_dir():
        used.update(number for path in specs.iterdir() if path.is_dir() for number in [_spec_number(path.name)] if number is not None)
    worktrees = GitWorktreeProvider().list_worktrees(project_path)
    used.update(number for record in worktrees for number in [_branch_spec_number(record.branch)] if number is not None)
    try:
        used.update(number for branch in git.local_branches(project_path) for number in [_branch_spec_number(branch)] if number is not None)
    except RepositoryProviderError:
        pass
    candidate = 1
    while candidate in used:
        candidate += 1
    return candidate


def _spec_number(name: str) -> int | None:
    match = re.match(r"^(\d{3})-", name)
    return int(match.group(1)) if match else None


def _branch_spec_number(branch: str) -> int | None:
    match = re.search(r"(?:^|/)(\d{3})-", branch)
    return int(match.group(1)) if match else None


def _run_id(project_id: str, spec: str, slug: str, base: str) -> str:
    raw = f"{project_id}:{spec}:{slug}:{base}".encode("utf-8")
    return hashlib.sha256(raw).hexdigest()[:24]


class _RunPlanError(RuntimeError):
    def __init__(self, code: str, message: str, evidence: dict[str, str]) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.evidence = evidence


@dataclass(frozen=True)
class _ResumeCandidate:
    record_path: Path
    record: WorkflowRunRecord

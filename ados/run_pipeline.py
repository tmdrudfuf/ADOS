"""End-to-end ADOS run pipeline orchestration."""

from __future__ import annotations

from dataclasses import asdict, dataclass
import ctypes
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import subprocess
from typing import Any, Protocol

from .exact_head_gate import ExactHeadGate
from .git_provider import GitRepositoryProvider
from .implementer_runtime import ImplementerRuntime, ImplementerRuntimeOutcome
from .no_change_verifier import NoChangeVerificationRequest, NoChangeVerificationResult, NoChangeVerificationViolation, NoChangeVerifier
from .primary_repository_guardian import PrimaryRepositoryGuardian
from .project_config import ProjectConfig
from .publication_engine import PublicationEngine, PublicationEvidence, PublicationGateResult
from .repository_provider import RepositoryProviderError
from .review_engine import ReviewEngine, ReviewRequest, ReviewResult, ReviewViolation, parse_review_decision
from .validation_engine import ValidationCommandResult, ValidationEngine, ValidationResult, ValidationViolation
from .worktree_lifecycle import WorktreeLifecycleEngine, WorktreeRequest, WorktreeLifecycleResult, WorktreeViolation


UNSAFE_TOKENS = ("&", "|", ";", "<", ">", "`", "$(", "\n", "\r")
PIPELINE_READY_STATUSES = {
    "READY_FOR_VALIDATION",
    "VALIDATION_FAILED",
    "READY_FOR_REVIEW",
    "REVIEW_BLOCKED",
    "REVIEW_CHANGES_REQUESTED",
    "REVIEW_APPROVED",
    "READY_FOR_PUBLICATION",
    "PR_CREATED",
    "PR_READY",
    "MERGED",
    "CLEANUP_INCOMPLETE",
    "NO_CHANGES_CLEANUP_INCOMPLETE",
}

TRANSIENT_REVIEW_FAILURE_CODES = {
    "REVIEWER_EXECUTABLE_NOT_FOUND",
    "REVIEWER_SPAWN_FAILED",
    "REVIEWER_COMMAND_FAILED",
    "REVIEWER_TIMED_OUT",
}
PR_REFRESH_ATTEMPTS = 3


@dataclass(frozen=True)
class PipelineStage:
    id: str
    status: str
    evidence: dict[str, str]

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass(frozen=True)
class PipelineViolation:
    code: str
    message: str
    evidence: dict[str, str]

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass(frozen=True)
class BootstrapCommandResult:
    command: str
    exit_code: int | None
    stdout: str
    stderr: str
    timed_out: bool

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass(frozen=True)
class CandidatePreparationResult:
    status: str
    candidate_sha: str
    changed_files: tuple[str, ...]
    violations: tuple[PipelineViolation, ...] = ()

    def to_dict(self) -> dict[str, object]:
        return {
            "status": self.status,
            "candidateSha": self.candidate_sha,
            "changedFiles": list(self.changed_files),
            "violations": [violation.to_dict() for violation in self.violations],
        }


@dataclass(frozen=True)
class PullRequestInfo:
    number: str
    url: str
    base_branch: str
    head_branch: str
    head_sha: str
    mergeable: bool
    draft: bool
    base_sha: str = ""
    merge_state_status: str = ""

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass(frozen=True)
class MergeResult:
    status: str
    merge_commit_sha: str
    violations: tuple[PipelineViolation, ...] = ()

    def to_dict(self) -> dict[str, object]:
        return {
            "status": self.status,
            "mergeCommitSha": self.merge_commit_sha,
            "violations": [violation.to_dict() for violation in self.violations],
        }


@dataclass(frozen=True)
class PipelineOutcome:
    status: str
    stages: tuple[PipelineStage, ...]
    run_record: dict[str, Any]
    bootstrap: tuple[BootstrapCommandResult, ...] = ()
    implementer_result: ImplementerRuntimeOutcome | None = None
    candidate: CandidatePreparationResult | None = None
    validation: ValidationResult | None = None
    no_change_verification: NoChangeVerificationResult | None = None
    review: ReviewResult | None = None
    exact_head_gate: dict[str, object] | None = None
    publication_gate: PublicationGateResult | None = None
    pull_request: PullRequestInfo | None = None
    merge: MergeResult | None = None
    cleanup: WorktreeLifecycleResult | None = None
    violations: tuple[PipelineViolation, ...] = ()

    def to_dict(self) -> dict[str, object]:
        return {
            "status": self.status,
            "stages": [stage.to_dict() for stage in self.stages],
            "runRecord": self.run_record,
            "bootstrap": [item.to_dict() for item in self.bootstrap],
            "implementerResult": self.implementer_result.to_dict() if self.implementer_result else None,
            "candidate": self.candidate.to_dict() if self.candidate else None,
            "validation": self.validation.to_dict() if self.validation else None,
            "noChangeVerification": self.no_change_verification.to_dict() if self.no_change_verification else None,
            "review": self.review.to_dict() if self.review else None,
            "exactHeadGate": self.exact_head_gate,
            "publicationGate": self.publication_gate.to_dict() if self.publication_gate else None,
            "pullRequest": self.pull_request.to_dict() if self.pull_request else None,
            "merge": self.merge.to_dict() if self.merge else None,
            "cleanup": self.cleanup.to_dict() if self.cleanup else None,
            "violations": [violation.to_dict() for violation in self.violations],
        }


class PublicationProvider(Protocol):
    def push(self, repo: Path, branch: str) -> PipelineViolation | None:
        ...

    def remote_head(self, repo: Path, branch: str) -> str:
        ...

    def create_or_get_draft_pr(self, repo: Path, *, base: str, head: str, title: str, body: str) -> PullRequestInfo | PipelineViolation:
        ...

    def mark_ready(self, repo: Path, number: str) -> PipelineViolation | None:
        ...

    def refresh_pr(self, repo: Path, number: str) -> PullRequestInfo | PipelineViolation:
        ...

    def merge(self, repo: Path, number: str, strategy: str, subject: str) -> MergeResult:
        ...

    def delete_remote_branch(self, repo: Path, branch: str) -> PipelineViolation | None:
        ...


class GitHubCliPublicationProvider:
    def push(self, repo: Path, branch: str) -> PipelineViolation | None:
        completed = _run(("git", "push", "-u", "origin", branch), repo)
        if completed.returncode != 0:
            return _violation("PUSH_FAILED", "feature branch push failed", {"stderr": completed.stderr})
        return None

    def remote_head(self, repo: Path, branch: str) -> str:
        _run(("git", "fetch", "origin", "--prune", "--quiet"), repo)
        completed = _run(("git", "rev-parse", f"origin/{branch}"), repo)
        return completed.stdout.strip() if completed.returncode == 0 else ""

    def create_or_get_draft_pr(self, repo: Path, *, base: str, head: str, title: str, body: str) -> PullRequestInfo | PipelineViolation:
        existing = _run(("gh", "pr", "list", "--head", head, "--json", "number,url,baseRefName,baseRefOid,headRefName,headRefOid,isDraft,mergeable,mergeStateStatus"), repo)
        if existing.returncode == 0:
            try:
                items = json.loads(existing.stdout)
            except json.JSONDecodeError:
                items = []
            if isinstance(items, list) and items:
                return _pr_info(items[0])
        created = _run(("gh", "pr", "create", "--draft", "--base", base, "--head", head, "--title", title, "--body", body), repo)
        if created.returncode != 0:
            return _violation("PR_CREATE_FAILED", "draft PR creation failed", {"stderr": created.stderr})
        return self._view_pr(repo, head)

    def mark_ready(self, repo: Path, number: str) -> PipelineViolation | None:
        completed = _run(("gh", "pr", "ready", number), repo)
        if completed.returncode != 0:
            return _violation("PR_READY_FAILED", "marking PR ready failed", {"stderr": completed.stderr})
        return None

    def refresh_pr(self, repo: Path, number: str) -> PullRequestInfo | PipelineViolation:
        return self._view_pr(repo, number)

    def merge(self, repo: Path, number: str, strategy: str, subject: str) -> MergeResult:
        flag = {"merge": "--merge", "squash": "--squash", "rebase": "--rebase"}[strategy]
        completed = _run(("gh", "pr", "merge", number, flag, "--delete-branch=false", "--subject", subject), repo)
        if completed.returncode != 0:
            return MergeResult("BLOCKED", "", (_violation("PR_MERGE_FAILED", "PR merge failed", {"stderr": completed.stderr}),))
        viewed = _run(("gh", "pr", "view", number, "--json", "mergeCommit,state"), repo)
        if viewed.returncode != 0:
            return MergeResult("BLOCKED", "", (_violation("PR_MERGE_VERIFY_FAILED", "merged PR could not be verified", {"stderr": viewed.stderr}),))
        try:
            raw = json.loads(viewed.stdout)
        except json.JSONDecodeError:
            raw = {}
        merge = raw.get("mergeCommit", {}) if isinstance(raw, dict) else {}
        merge_sha = str(merge.get("oid", "")) if isinstance(merge, dict) else ""
        if raw.get("state") != "MERGED" or not merge_sha:
            return MergeResult("BLOCKED", merge_sha, (_violation("PR_NOT_MERGED", "PR did not resolve to merged state", {"state": str(raw.get("state", ""))}),))
        return MergeResult("MERGED", merge_sha)

    def delete_remote_branch(self, repo: Path, branch: str) -> PipelineViolation | None:
        completed = _run(("git", "push", "origin", "--delete", branch), repo)
        if completed.returncode != 0:
            if _already_deleted(completed.stderr + completed.stdout):
                return None
            return _violation("REMOTE_BRANCH_DELETE_FAILED", "remote branch deletion failed", {"stderr": completed.stderr})
        return None

    def _view_pr(self, repo: Path, head: str) -> PullRequestInfo | PipelineViolation:
        viewed = _run(("gh", "pr", "view", head, "--json", "number,url,baseRefName,baseRefOid,headRefName,headRefOid,isDraft,mergeable,mergeStateStatus"), repo)
        if viewed.returncode != 0:
            return _violation("PR_VERIFY_FAILED", "PR could not be verified", {"stderr": viewed.stderr})
        try:
            return _pr_info(json.loads(viewed.stdout))
        except json.JSONDecodeError:
            return _violation("PR_VERIFY_INVALID_JSON", "PR verification returned invalid JSON", {})


class RunPipeline:
    def __init__(
        self,
        *,
        git: GitRepositoryProvider | None = None,
        guardian: PrimaryRepositoryGuardian | None = None,
        implementer: ImplementerRuntime | None = None,
        validation: ValidationEngine | None = None,
        review: ReviewEngine | None = None,
        no_change_verifier: NoChangeVerifier | None = None,
        exact_head: ExactHeadGate | None = None,
        publication: PublicationEngine | None = None,
        publisher: PublicationProvider | None = None,
        lifecycle: WorktreeLifecycleEngine | None = None,
    ) -> None:
        self.git = git or GitRepositoryProvider()
        self.guardian = guardian or PrimaryRepositoryGuardian()
        self.implementer = implementer or ImplementerRuntime()
        self.validation = validation or ValidationEngine()
        self.review = review or ReviewEngine()
        self.no_change_verifier = no_change_verifier or NoChangeVerifier()
        self.exact_head = exact_head or ExactHeadGate()
        self.publication = publication or PublicationEngine()
        self.publisher = publisher or GitHubCliPublicationProvider()
        self.lifecycle = lifecycle or WorktreeLifecycleEngine()

    def run(self, *, config: ProjectConfig, run_record_path: Path, timeout_ms: int) -> PipelineOutcome:
        stages: list[PipelineStage] = []
        record = _read_json(run_record_path)
        if not isinstance(record, dict):
            return PipelineOutcome("BLOCKED", (_stage("record", "BLOCKED", {}),), {}, violations=(_violation("RUN_RECORD_INVALID", "run record is unavailable", {"path": str(run_record_path)}),))

        if record.get("status") == "NO_CHANGES_CLEANUP_INCOMPLETE":
            return self._resume_no_changes_cleanup(config, run_record_path, record, stages)
        if record.get("status") in {"MERGED", "CLEANUP_INCOMPLETE"}:
            return self._resume_cleanup(config, run_record_path, record, stages)
        if record.get("status") in {"REVIEW_APPROVED", "READY_FOR_PUBLICATION", "PR_CREATED", "PR_READY"}:
            resumed = self._resume_publication(config, run_record_path, record, stages)
            if resumed is not None:
                return resumed
        if record.get("status") == "REVIEW_BLOCKED":
            return self._resume_review(config, run_record_path, record, stages, timeout_ms)
        if record.get("status") == "VALIDATION_FAILED":
            candidate_artifact = _read_run_artifact(run_record_path, record, "candidate.json")
            validation_artifact = _read_run_artifact(run_record_path, record, "validation-runtime.json")
            resumable = validation_failed_evidence(
                candidate_artifact,
                validation_artifact,
            )
            if resumable:
                return PipelineOutcome("VALIDATION_FAILED", tuple([*stages, _stage("validation_resume", "BLOCKED", {})]), record, violations=resumable)
            _ensure_validation_failure_evidence(run_record_path, record, candidate_artifact, validation_artifact)
            record = _read_json(run_record_path) or record
            adoption = self._adopt_validation_recovery_candidate(config, run_record_path, record, candidate_artifact, validation_artifact, stages)
            if isinstance(adoption, PipelineOutcome):
                return adoption
            if adoption is not None:
                record, candidate_artifact = adoption
                stages.append(_stage("recovery_candidate_adoption", "PASS", {"candidate_sha": str(candidate_artifact.get("candidate_sha", ""))}))
            else:
                blocked = _validation_recovery_block_violation(record)
                if blocked is not None:
                    return PipelineOutcome("VALIDATION_FAILED", tuple([*stages, _stage("validation_recovery", "BLOCKED", {})]), record, violations=(blocked,))

        bootstrap = self._bootstrap(config, record, run_record_path)
        stages.append(_stage("bootstrap", "PASS" if all(item.exit_code == 0 for item in bootstrap) else "BLOCKED", {"commands": str(len(bootstrap))}))
        if any(item.exit_code != 0 for item in bootstrap):
            return PipelineOutcome("BOOTSTRAP_FAILED", tuple(stages), record, bootstrap=bootstrap, violations=(_violation("BOOTSTRAP_FAILED", "bootstrap command failed", {}),))

        if record.get("status") in {"READY_FOR_IMPLEMENTATION", "IMPLEMENTATION_FAILED", "IMPLEMENTATION_TIMED_OUT", "VALIDATION_FAILED"}:
            implementer_result = self.implementer.run(config=config, run_record_path=run_record_path, timeout_ms=timeout_ms)
            record = implementer_result.run_record or record
            stages.append(_stage("implementer", implementer_result.status, {}))
            if implementer_result.status != "READY_FOR_VALIDATION":
                return PipelineOutcome(implementer_result.status, tuple(stages), record, bootstrap=bootstrap, implementer_result=implementer_result, violations=tuple(_from_implementer(item) for item in implementer_result.violations))
        else:
            implementer_result = None
            stages.append(_stage("implementer", "SKIPPED", {"status": str(record.get("status", ""))}))

        max_rounds = config.execution_policy.review.max_rounds
        review_result: ReviewResult | None = None
        validation_result: ValidationResult | None = None
        candidate_result: CandidatePreparationResult | None = None
        round_number = 1
        while round_number <= max_rounds:
            candidate_result = self._prepare_candidate(config, record)
            stages.append(_stage("candidate", candidate_result.status, {"candidate_sha": candidate_result.candidate_sha, "round": str(round_number)}))
            if candidate_result.status == "BLOCKED":
                return PipelineOutcome("CANDIDATE_BLOCKED", tuple(stages), record, bootstrap=bootstrap, implementer_result=implementer_result, candidate=candidate_result, violations=candidate_result.violations)
            if candidate_result.status == "NO_CHANGES":
                adjudication = self._adjudicate_no_changes(config, run_record_path, record, stages, bootstrap, implementer_result, candidate_result, timeout_ms)
                if isinstance(adjudication, PipelineOutcome):
                    return adjudication
                implementer_result, record = adjudication
                continue
            _record_validation_recovery_candidate(run_record_path, record, candidate_result)
            record = _read_json(run_record_path) or record
            _write_status(run_record_path, record, "READY_FOR_VALIDATION")

            validation_result = self.validation.run(policy=config.execution_policy, repository_path=Path(record["featureWorktree"]))
            _write_json(run_record_path.with_name("validation-runtime.json"), validation_result.to_dict())
            stages.append(_stage("validation", validation_result.status, {"validated_sha": validation_result.head_after}))
            if validation_result.status != "PASS" or validation_result.head_before != validation_result.head_after:
                _write_validation_failure_status(run_record_path, record, candidate_result, validation_result)
                recovery = self._recover_validation_failure(
                    config,
                    run_record_path,
                    _read_json(run_record_path),
                    candidate_result,
                    validation_result,
                    stages,
                    bootstrap,
                    implementer_result,
                    timeout_ms,
                )
                if isinstance(recovery, PipelineOutcome):
                    return recovery
                implementer_result, record = recovery
                continue

            diff = _git_output(Path(record["featureWorktree"]), "diff", "--no-ext-diff", "--no-color", f"{record['authoritativeBaseSha']}..{candidate_result.candidate_sha}")
            review_scope = f"specs/{record['specNumber']}-{record['featureSlug']}"
            if not (Path(record["featureWorktree"]) / review_scope).exists():
                review_scope = str(record["featureDescription"])
            review_artifact_snapshot = _review_artifact_snapshot(Path(record["featureWorktree"]), review_scope)
            review_result = self.review.run(
                policy=config.execution_policy,
                request=ReviewRequest(
                    repository_path=Path(record["featureWorktree"]),
                    candidate_sha=candidate_result.candidate_sha,
                    base_sha=record["authoritativeBaseSha"],
                    scope=review_scope,
                    diff=diff,
                ),
            )
            _write_json(run_record_path.with_name("review-runtime.json"), review_result.to_dict())
            side_effect_violations = _isolate_review_artifacts(Path(record["featureWorktree"]), run_record_path, record, review_scope, candidate_result.candidate_sha, review_artifact_snapshot)
            if side_effect_violations:
                _write_review_block_status(run_record_path, record, review_result, candidate_result, validation_result, block_violations=side_effect_violations, block_cause="review_side_effect")
                return PipelineOutcome("REVIEW_BLOCKED", tuple(stages), _read_json(run_record_path), bootstrap=bootstrap, implementer_result=implementer_result, candidate=candidate_result, validation=validation_result, review=review_result, violations=side_effect_violations)
            stages.append(_stage("review", review_result.decision, {"reviewed_sha": review_result.reviewed_sha, "round": str(round_number)}))
            if review_result.status != "PASS":
                _write_review_block_status(run_record_path, record, review_result, candidate_result, validation_result)
                return PipelineOutcome("REVIEW_BLOCKED", tuple(stages), _read_json(run_record_path), bootstrap=bootstrap, implementer_result=implementer_result, candidate=candidate_result, validation=validation_result, review=review_result, violations=tuple(_from_review(item) for item in review_result.violations))
            if review_result.decision == "Approved":
                _write_status(run_record_path, record, "REVIEW_APPROVED")
                break
            if review_result.decision != "Changes Requested":
                _write_review_block_status(run_record_path, record, review_result, candidate_result, validation_result, block_violations=(_violation("REVIEW_DECISION_UNAVAILABLE", "review decision was not Approved or Changes Requested", {}),), block_cause="review_decision_unavailable")
                return PipelineOutcome("REVIEW_BLOCKED", tuple(stages), _read_json(run_record_path), bootstrap=bootstrap, implementer_result=implementer_result, candidate=candidate_result, validation=validation_result, review=review_result, violations=(_violation("REVIEW_DECISION_UNAVAILABLE", "review decision was not Approved or Changes Requested", {}),))
            if round_number == max_rounds:
                _write_review_block_status(run_record_path, record, review_result, candidate_result, validation_result)
                return PipelineOutcome("REVIEW_BLOCKED", tuple(stages), _read_json(run_record_path), bootstrap=bootstrap, implementer_result=implementer_result, candidate=candidate_result, validation=validation_result, review=review_result, violations=(_violation("REVIEW_MAX_ROUNDS_EXCEEDED", "review changes requested after max rounds", {"max_rounds": str(max_rounds)}),))
            _write_status(run_record_path, record, "READY_FOR_IMPLEMENTATION")
            implementer_result = self.implementer.run(config=config, run_record_path=run_record_path, timeout_ms=timeout_ms)
            record = implementer_result.run_record or _read_json(run_record_path)
            stages.append(_stage("implementer_fix", implementer_result.status, {"round": str(round_number)}))
            if implementer_result.status != "READY_FOR_VALIDATION":
                return PipelineOutcome(implementer_result.status, tuple(stages), record, bootstrap=bootstrap, implementer_result=implementer_result, candidate=candidate_result, validation=validation_result, review=review_result, violations=tuple(_from_implementer(item) for item in implementer_result.violations))
            round_number += 1

        if review_result is None or validation_result is None or candidate_result is None:
            return PipelineOutcome("BLOCKED", tuple(stages), record, violations=(_violation("PIPELINE_INCOMPLETE", "pipeline did not produce validation and review evidence", {}),))

        exact = self.exact_head.verify(repository_path=Path(record["featureWorktree"]), approved_review_sha=review_result.reviewed_sha, validated_sha=validation_result.head_after)
        stages.append(_stage("exact_head", exact.status, {"approved_review_sha": review_result.reviewed_sha, "validated_sha": validation_result.head_after}))
        if exact.status != "MATCH":
            return PipelineOutcome("EXACT_HEAD_BLOCKED", tuple(stages), _read_json(run_record_path), bootstrap=bootstrap, implementer_result=implementer_result, candidate=candidate_result, validation=validation_result, review=review_result, exact_head_gate=exact.to_dict(), violations=tuple(PipelineViolation(item.code, item.message, item.evidence) for item in exact.violations))

        return self._publish(config, run_record_path, record, stages, bootstrap, implementer_result, candidate_result, validation_result, review_result, exact.to_dict())

    def _bootstrap(self, config: ProjectConfig, record: dict[str, Any], run_record_path: Path) -> tuple[BootstrapCommandResult, ...]:
        evidence_path = run_record_path.with_name("bootstrap-runtime.json")
        existing = _read_json(evidence_path)
        if isinstance(existing, dict) and existing.get("status") == "PASS":
            return tuple(BootstrapCommandResult(**item) for item in existing.get("commands", []))
        results: list[BootstrapCommandResult] = []
        for command in config.bootstrap_commands:
            if any(token in command for token in UNSAFE_TOKENS):
                result = BootstrapCommandResult(command, None, "", "unsafe command", False)
            else:
                try:
                    parts = _split_command(command)
                    executable = _resolve_executable(parts[0])
                    completed = subprocess.run((executable, *parts[1:]), cwd=Path(record["featureWorktree"]), shell=False, capture_output=True, text=True, encoding="utf-8", errors="replace")
                    result = BootstrapCommandResult(command, completed.returncode, _bounded(completed.stdout), _bounded(completed.stderr), False)
                except (FileNotFoundError, OSError) as exc:
                    result = BootstrapCommandResult(command, None, "", str(exc), False)
            results.append(result)
            if result.exit_code != 0:
                break
        _write_json(evidence_path, {"status": "PASS" if all(item.exit_code == 0 for item in results) else "BLOCK", "commands": [item.to_dict() for item in results]})
        return tuple(results)

    def _recover_validation_failure(
        self,
        config: ProjectConfig,
        run_record_path: Path,
        record: dict[str, Any],
        candidate: CandidatePreparationResult,
        validation: ValidationResult,
        stages: list[PipelineStage],
        bootstrap: tuple[BootstrapCommandResult, ...],
        previous_implementer: ImplementerRuntimeOutcome | None,
        timeout_ms: int,
    ) -> tuple[ImplementerRuntimeOutcome, dict[str, Any]] | PipelineOutcome:
        max_rounds = config.execution_policy.validation.max_recovery_rounds
        round_number = _validation_recovery_attempt_count(record) + 1
        if round_number > max_rounds:
            violation = _violation(
                "VALIDATION_RECOVERY_MAX_ROUNDS_EXCEEDED",
                "validation recovery reached the configured maximum recovery rounds",
                {"max_recovery_rounds": str(max_rounds), "candidate_sha": candidate.candidate_sha},
            )
            _write_validation_recovery_block_status(run_record_path, record, violation)
            return PipelineOutcome(
                "VALIDATION_FAILED",
                tuple([*stages, _stage("validation_recovery", "BLOCKED", {"round": str(round_number - 1), "max_rounds": str(max_rounds)})]),
                _read_json(run_record_path),
                bootstrap=bootstrap,
                implementer_result=previous_implementer,
                candidate=candidate,
                validation=validation,
                violations=tuple([*(_from_validation(item) for item in validation.violations), violation]),
            )

        _append_validation_recovery_attempt(run_record_path, record, candidate, validation, round_number, max_rounds)
        recovery_record = _read_json(run_record_path) or record
        implementer_result = self.implementer.run(config=config, run_record_path=run_record_path, timeout_ms=timeout_ms)
        _update_validation_recovery_attempt(run_record_path, round_number, {"implementerStatus": implementer_result.status, "implementerViolationCodes": [item.code for item in implementer_result.violations]})
        updated_record = _read_json(run_record_path) or implementer_result.run_record or recovery_record
        stages.append(_stage("validation_recovery_implementer", implementer_result.status, {"round": str(round_number), "failed_candidate_sha": candidate.candidate_sha}))
        if implementer_result.status != "READY_FOR_VALIDATION":
            return PipelineOutcome(
                implementer_result.status,
                tuple(stages),
                updated_record,
                bootstrap=bootstrap,
                implementer_result=implementer_result,
                candidate=candidate,
                validation=validation,
                violations=tuple(_from_implementer(item) for item in implementer_result.violations),
            )

        worktree = Path(record["featureWorktree"])
        try:
            status = self.git.status(worktree)
            current_head = self.git.current_head(worktree)
            branch = self.git.current_branch(worktree)
        except RepositoryProviderError as exc:
            violation = _violation(exc.code, exc.message, {"worktree": str(worktree)})
            _write_validation_recovery_block_status(run_record_path, updated_record, violation)
            return PipelineOutcome("VALIDATION_FAILED", tuple([*stages, _stage("validation_recovery", "BLOCKED", {})]), _read_json(run_record_path), bootstrap=bootstrap, implementer_result=implementer_result, candidate=candidate, validation=validation, violations=(violation,))
        if branch != str(record.get("featureBranch", "")):
            violation = _violation("VALIDATION_RECOVERY_BRANCH_MISMATCH", "validation recovery requires the recorded feature branch", {"expected": str(record.get("featureBranch", "")), "actual": branch})
            _write_validation_recovery_block_status(run_record_path, updated_record, violation)
            return PipelineOutcome("VALIDATION_FAILED", tuple([*stages, _stage("validation_recovery", "BLOCKED", {"branch": branch})]), _read_json(run_record_path), bootstrap=bootstrap, implementer_result=implementer_result, candidate=candidate, validation=validation, violations=(violation,))
        if current_head == candidate.candidate_sha and not (status.staged or status.dirty_tracked or status.untracked):
            violation = _violation("VALIDATION_RECOVERY_NO_CHANGES", "validation recovery implementer produced no changes for the failed candidate", {"candidate_sha": candidate.candidate_sha, "round": str(round_number)})
            _write_validation_recovery_block_status(run_record_path, updated_record, violation)
            return PipelineOutcome("VALIDATION_FAILED", tuple([*stages, _stage("validation_recovery", "BLOCKED", {"round": str(round_number)})]), _read_json(run_record_path), bootstrap=bootstrap, implementer_result=implementer_result, candidate=candidate, validation=validation, violations=(violation,))

        _update_validation_recovery_attempt(run_record_path, round_number, {"status": "READY_FOR_VALIDATION", "headAfterImplementer": current_head, "changedFilesAfterImplementer": list(status.staged + status.dirty_tracked + status.untracked)})
        return implementer_result, _read_json(run_record_path) or updated_record

    def _prepare_candidate(self, config: ProjectConfig, record: dict[str, Any]) -> CandidatePreparationResult:
        primary = Path(record["primaryRepository"])
        worktree = Path(record["featureWorktree"])
        guardian = self.guardian.audit(policy=config.execution_policy, repository_path=primary, expected_repository_path=config.primary_repository_path, expected_branch=config.default_branch, allowed_local_paths=config.allowed_primary_local_paths)
        if guardian.status == "BLOCK":
            return CandidatePreparationResult("BLOCKED", "", (), tuple(PipelineViolation(f"PRIMARY_{item.code}", item.message, item.evidence) for item in guardian.violations))
        diff_check = _run(("git", "diff", "--check"), worktree)
        if diff_check.returncode != 0:
            return CandidatePreparationResult("BLOCKED", "", (), (_violation("DIFF_CHECK_FAILED", "candidate diff check failed", {"stderr": diff_check.stderr}),))
        try:
            status = self.git.status(worktree)
        except RepositoryProviderError as exc:
            return CandidatePreparationResult("BLOCKED", "", (), (_violation(exc.code, exc.message, {"worktree": str(worktree)}),))
        changed = tuple(status.staged + status.dirty_tracked + status.untracked)
        if not changed:
            adopted_candidate = _adopted_candidate_from_record(record, status.head)
            if adopted_candidate is not None:
                return adopted_candidate
        if changed:
            add = _run(("git", "add", "-A"), worktree)
            if add.returncode != 0:
                return CandidatePreparationResult("BLOCKED", status.head, changed, (_violation("GIT_ADD_FAILED", "staging candidate changes failed", {"stderr": add.stderr}),))
            commit = _run(("git", "commit", "-m", _commit_message(record)), worktree)
            if commit.returncode != 0:
                return CandidatePreparationResult("BLOCKED", status.head, changed, (_violation("GIT_COMMIT_FAILED", "candidate commit failed", {"stderr": commit.stderr}),))
        try:
            head = self.git.current_head(worktree)
        except RepositoryProviderError as exc:
            return CandidatePreparationResult("BLOCKED", "", changed, (_violation(exc.code, exc.message, {"worktree": str(worktree)}),))
        _write_json(Path(record["featureWorktree"]) / ".agent-workflow" / "runs" / f"{record['specNumber']}-{record['featureSlug']}" / "candidate.json", {"status": "COMMITTED", "candidate_sha": head, "changed_files": list(changed)})
        no_delta = _no_change_violations(self.git, worktree, record, head)
        if not no_delta:
            _write_json(Path(record["featureWorktree"]) / ".agent-workflow" / "runs" / f"{record['specNumber']}-{record['featureSlug']}" / "candidate.json", {"status": "NO_CHANGES", "candidate_sha": head, "changed_files": list(changed)})
            return CandidatePreparationResult("NO_CHANGES", head, changed)
        return CandidatePreparationResult("COMMITTED", head, changed)

    def _adjudicate_no_changes(
        self,
        config: ProjectConfig,
        run_record_path: Path,
        record: dict[str, Any],
        stages: list[PipelineStage],
        bootstrap: tuple[BootstrapCommandResult, ...],
        implementer_result: ImplementerRuntimeOutcome | None,
        candidate: CandidatePreparationResult,
        timeout_ms: int,
    ) -> tuple[ImplementerRuntimeOutcome, dict[str, Any]] | PipelineOutcome:
        round_number = _no_change_adjudication_attempt_count(record) + 1
        max_rounds = config.execution_policy.validation.max_no_change_recovery_rounds
        verification = self.no_change_verifier.run(
            policy=config.execution_policy,
            request=NoChangeVerificationRequest(
                repository_path=Path(record["featureWorktree"]),
                spec_number=str(record["specNumber"]),
                feature_description=str(record["featureDescription"]),
                candidate_sha=candidate.candidate_sha,
                base_sha=str(record["authoritativeBaseSha"]),
                implementer_status=candidate.status,
            ),
        )
        _write_json(run_record_path.with_name("no-change-verification-runtime.json"), verification.to_dict())
        _append_no_change_adjudication(run_record_path, record, candidate, verification, round_number, max_rounds)
        record = _read_json(run_record_path) or record
        stages.append(_stage("no_change_verification", verification.decision, {"round": str(round_number), "candidate_sha": candidate.candidate_sha}))
        if verification.status != "PASS":
            violation = _violation("NO_CHANGES_AMBIGUOUS", "no-change verification did not produce a trusted decision", {"candidate_sha": candidate.candidate_sha})
            _write_no_change_adjudication_block(run_record_path, record, violation)
            return PipelineOutcome("NO_CHANGES_AMBIGUOUS", tuple(stages), _read_json(run_record_path), bootstrap=bootstrap, implementer_result=implementer_result, candidate=candidate, no_change_verification=verification, violations=tuple([*(_from_no_change_verification(item) for item in verification.violations), violation]))
        if verification.decision == "NO_CHANGES_VERIFIED":
            return self._finalize_no_changes(config, run_record_path, record, stages, bootstrap, implementer_result, candidate, no_change_verification=verification)
        if verification.decision == "AMBIGUOUS":
            violation = _violation("NO_CHANGES_AMBIGUOUS", "verifier could not determine whether the feature is already satisfied", {"candidate_sha": candidate.candidate_sha})
            _write_no_change_adjudication_block(run_record_path, record, violation)
            return PipelineOutcome("NO_CHANGES_AMBIGUOUS", tuple(stages), _read_json(run_record_path), bootstrap=bootstrap, implementer_result=implementer_result, candidate=candidate, no_change_verification=verification, violations=(violation,))

        if round_number > max_rounds:
            violation = _violation("NO_CHANGE_RECOVERY_MAX_ROUNDS_EXCEEDED", "no-change recovery reached the configured maximum recovery rounds", {"max_no_change_recovery_rounds": str(max_rounds), "candidate_sha": candidate.candidate_sha})
            _write_no_change_adjudication_block(run_record_path, record, violation)
            return PipelineOutcome("NO_CHANGES_AMBIGUOUS", tuple(stages), _read_json(run_record_path), bootstrap=bootstrap, implementer_result=implementer_result, candidate=candidate, no_change_verification=verification, violations=(violation,))

        _write_no_change_recovery_status(run_record_path, record, verification)
        recovery_record = _read_json(run_record_path) or record
        recovery_implementer = self.implementer.run(config=config, run_record_path=run_record_path, timeout_ms=timeout_ms)
        stages.append(_stage("no_change_recovery_implementer", recovery_implementer.status, {"round": str(round_number)}))
        _update_no_change_adjudication(run_record_path, round_number, {"recoveryImplementerStatus": recovery_implementer.status, "recoveryImplementerViolationCodes": [item.code for item in recovery_implementer.violations]})
        updated_record = _read_json(run_record_path) or recovery_implementer.run_record or recovery_record
        if recovery_implementer.status != "READY_FOR_VALIDATION":
            return PipelineOutcome(recovery_implementer.status, tuple(stages), updated_record, bootstrap=bootstrap, implementer_result=recovery_implementer, candidate=candidate, no_change_verification=verification, violations=tuple(_from_implementer(item) for item in recovery_implementer.violations))
        return recovery_implementer, updated_record

    def _finalize_no_changes(
        self,
        config: ProjectConfig,
        run_record_path: Path,
        record: dict[str, Any],
        stages: list[PipelineStage],
        bootstrap: tuple[BootstrapCommandResult, ...],
        implementer_result: ImplementerRuntimeOutcome | None,
        candidate: CandidatePreparationResult,
        no_change_verification: NoChangeVerificationResult | None = None,
    ) -> PipelineOutcome:
        violations = _no_change_violations(self.git, Path(record["featureWorktree"]), record, candidate.candidate_sha)
        if violations:
            _write_status(run_record_path, record, "CANDIDATE_BLOCKED")
            return PipelineOutcome("CANDIDATE_BLOCKED", tuple(stages), _read_json(run_record_path), bootstrap=bootstrap, implementer_result=implementer_result, candidate=candidate, violations=violations)
        no_change_archive = _archive_no_change(Path(record["primaryRepository"]), record, candidate)
        primary_record = _archive_run_record(Path(record["primaryRepository"]), {**record, "noChangeArchive": str(no_change_archive)}, "NO_CHANGES_CLEANUP_INCOMPLETE")
        cleanup = self._cleanup(config, record)
        stages.append(_stage("cleanup", cleanup.status, {"archive": str(no_change_archive)}))
        if cleanup.status != "PASS":
            return PipelineOutcome("NO_CHANGES_CLEANUP_INCOMPLETE", tuple(stages), _read_json(primary_record), bootstrap=bootstrap, implementer_result=implementer_result, candidate=candidate, no_change_verification=no_change_verification, cleanup=cleanup, violations=tuple(PipelineViolation(item.code, item.message, item.evidence) for item in cleanup.violations))
        local_delete = _run(("git", "branch", "-d", record["featureBranch"]), Path(record["primaryRepository"]))
        if local_delete.returncode != 0 and "not found" not in (local_delete.stderr + local_delete.stdout).lower():
            violation = _violation("LOCAL_BRANCH_DELETE_FAILED", "local no-change feature branch deletion failed", {"stderr": local_delete.stderr})
            return PipelineOutcome("NO_CHANGES_CLEANUP_INCOMPLETE", tuple(stages), _read_json(primary_record), bootstrap=bootstrap, implementer_result=implementer_result, candidate=candidate, no_change_verification=no_change_verification, cleanup=cleanup, violations=(violation,))
        _write_status(primary_record, {**record, "noChangeArchive": str(no_change_archive)}, "NO_CHANGES")
        return PipelineOutcome("NO_CHANGES", tuple(stages), _read_json(primary_record), bootstrap=bootstrap, implementer_result=implementer_result, candidate=candidate, no_change_verification=no_change_verification, cleanup=cleanup)

    def _publish(
        self,
        config: ProjectConfig,
        run_record_path: Path,
        record: dict[str, Any],
        stages: list[PipelineStage],
        bootstrap: tuple[BootstrapCommandResult, ...],
        implementer_result: ImplementerRuntimeOutcome | None,
        candidate: CandidatePreparationResult,
        validation: ValidationResult,
        review: ReviewResult,
        exact: dict[str, object],
    ) -> PipelineOutcome:
        worktree = Path(record["featureWorktree"])
        if isinstance(self.publisher, GitHubCliPublicationProvider) and not _is_github_origin(self.git, worktree):
            _write_status(run_record_path, record, "READY_FOR_PUBLICATION")
            stages.append(_stage("publication", "READY_FOR_PUBLICATION", {"reason": "non_github_origin"}))
            return PipelineOutcome("READY_FOR_PUBLICATION", tuple(stages), _read_json(run_record_path), bootstrap=bootstrap, implementer_result=implementer_result, candidate=candidate, validation=validation, review=review, exact_head_gate=exact)
        push_violation = self.publisher.push(worktree, record["featureBranch"])
        if push_violation:
            _write_publication_status(run_record_path, record, "READY_FOR_PUBLICATION")
            return PipelineOutcome("PUBLICATION_BLOCKED", tuple(stages), _read_json(run_record_path), bootstrap=bootstrap, implementer_result=implementer_result, candidate=candidate, validation=validation, review=review, exact_head_gate=exact, violations=(push_violation,))
        remote_sha = self.publisher.remote_head(worktree, record["featureBranch"])
        _write_publication_status(run_record_path, {**record, "remoteBranchHeadSha": remote_sha}, "READY_FOR_PUBLICATION")
        stages.append(_stage("push", "PASS" if remote_sha == candidate.candidate_sha else "BLOCKED", {"remote_head": remote_sha}))
        pr = self.publisher.create_or_get_draft_pr(worktree, base=config.default_branch, head=record["featureBranch"], title=_commit_message(record), body=f"ADOS run {record['runId']}\n\nValidated SHA: {candidate.candidate_sha}\nReview: {review.decision}")
        if isinstance(pr, PipelineViolation):
            return PipelineOutcome("PUBLICATION_BLOCKED", tuple(stages), _read_json(run_record_path), bootstrap=bootstrap, implementer_result=implementer_result, candidate=candidate, validation=validation, review=review, exact_head_gate=exact, violations=(pr,))
        _write_publication_status(run_record_path, _record_with_pr(record, pr, remote_sha), "PR_CREATED")
        ready_violation = self.publisher.mark_ready(worktree, pr.number)
        if ready_violation:
            return PipelineOutcome("PUBLICATION_BLOCKED", tuple(stages), _read_json(run_record_path), bootstrap=bootstrap, implementer_result=implementer_result, candidate=candidate, validation=validation, review=review, exact_head_gate=exact, pull_request=pr, violations=(ready_violation,))
        _write_publication_status(run_record_path, {**_record_with_pr(record, pr, remote_sha), "prReady": "true"}, "PR_READY")
        stages.append(_stage("pr", "READY", {"number": pr.number, "head_sha": pr.head_sha}))
        refreshed_pr = _refresh_pr_for_gate(self.publisher, worktree, pr, config.default_branch, str(record["authoritativeBaseSha"]), record["featureBranch"], candidate.candidate_sha)
        if isinstance(refreshed_pr, PipelineViolation):
            return PipelineOutcome("PUBLICATION_BLOCKED", tuple([*stages, _stage("pr_refresh", "BLOCKED", {})]), _read_json(run_record_path), bootstrap=bootstrap, implementer_result=implementer_result, candidate=candidate, validation=validation, review=review, exact_head_gate=exact, pull_request=pr, violations=(refreshed_pr,))
        pr = refreshed_pr
        _write_publication_status(run_record_path, {**_record_with_pr(record, pr, remote_sha), "prReady": str(not pr.draft).lower()}, "PR_READY")
        stages.append(_stage("pr_refresh", "PASS", {"number": pr.number, "head_sha": pr.head_sha, "merge_state": pr.merge_state_status}))
        gate = self.publication.evaluate(
            policy=config.execution_policy,
            evidence=PublicationEvidence(
                review_decision=review.decision,
                blocking_findings=(),
                validation_passed=validation.status == "PASS",
                approved_review_sha=review.reviewed_sha,
                validated_sha=validation.head_after,
                local_head_sha=candidate.candidate_sha,
                remote_branch_head_sha=remote_sha,
                pr_head_sha=pr.head_sha,
                exact_head_gate=str(exact.get("status", "")),
                primary_repository_audit="SAFE",
                feature_worktree_clean=_is_clean(self.git, worktree),
                intended_base_branch=config.default_branch,
                intended_head_branch=record["featureBranch"],
                pr_base_branch=pr.base_branch,
                pr_head_branch=pr.head_branch,
                pr_mergeable=pr.mergeable,
                unresolved_blocking_review_state=False,
                post_approval_commit=False,
                safety_recovery_active=False,
                scope_approved=True,
                merge_strategy=config.execution_policy.publication.merge_strategy,
                force_push_required=False,
                history_rewrite_required=False,
                bypass_required=False,
            ),
        )
        stages.append(_stage("publication_gate", gate.status, {}))
        if gate.status != "PERMITTED":
            return PipelineOutcome("PUBLICATION_BLOCKED", tuple(stages), _read_json(run_record_path), bootstrap=bootstrap, implementer_result=implementer_result, candidate=candidate, validation=validation, review=review, exact_head_gate=exact, publication_gate=gate, pull_request=pr, violations=tuple(PipelineViolation(item.code, item.message, item.evidence) for item in gate.violations))
        merge = self.publisher.merge(worktree, pr.number, config.execution_policy.publication.merge_strategy, _commit_message(record))
        stages.append(_stage("merge", merge.status, {"merge_commit": merge.merge_commit_sha}))
        if merge.status != "MERGED":
            return PipelineOutcome("PUBLICATION_BLOCKED", tuple(stages), _read_json(run_record_path), bootstrap=bootstrap, implementer_result=implementer_result, candidate=candidate, validation=validation, review=review, exact_head_gate=exact, publication_gate=gate, pull_request=pr, merge=merge, violations=merge.violations)
        _write_publication_status(run_record_path, {**_record_with_pr(record, pr, remote_sha), "mergeCommitSha": merge.merge_commit_sha}, "MERGED")
        primary_update_violations = _update_primary_main(Path(record["primaryRepository"]), config.default_branch)
        if primary_update_violations:
            _write_publication_status(run_record_path, {**_record_with_pr(record, pr, remote_sha), "mergeCommitSha": merge.merge_commit_sha}, "CLEANUP_INCOMPLETE")
            primary_record = _archive_run_record(Path(record["primaryRepository"]), {**record, "pullRequest": pr.number, "mergeCommitSha": merge.merge_commit_sha}, "CLEANUP_INCOMPLETE")
            return PipelineOutcome("CLEANUP_INCOMPLETE", tuple(stages), _read_json(primary_record), bootstrap=bootstrap, implementer_result=implementer_result, candidate=candidate, validation=validation, review=review, exact_head_gate=exact, publication_gate=gate, pull_request=pr, merge=merge, violations=primary_update_violations)
        archive = _archive_evidence(Path(record["primaryRepository"]), record, candidate, validation, review, pr, merge)
        primary_record = _archive_run_record(Path(record["primaryRepository"]), {**record, "pullRequest": pr.number, "mergeCommitSha": merge.merge_commit_sha}, "MERGED")
        remote_delete = self.publisher.delete_remote_branch(Path(record["primaryRepository"]), record["featureBranch"])
        if remote_delete:
            _write_publication_status(run_record_path, {**record, "pullRequest": pr.number, "mergeCommitSha": merge.merge_commit_sha}, "CLEANUP_INCOMPLETE")
            _write_status(primary_record, {**record, "pullRequest": pr.number, "mergeCommitSha": merge.merge_commit_sha}, "CLEANUP_INCOMPLETE")
            return PipelineOutcome("CLEANUP_INCOMPLETE", tuple(stages), _read_json(primary_record), bootstrap=bootstrap, implementer_result=implementer_result, candidate=candidate, validation=validation, review=review, exact_head_gate=exact, publication_gate=gate, pull_request=pr, merge=merge, violations=(remote_delete,))
        cleanup = self._cleanup(config, record)
        stages.append(_stage("cleanup", cleanup.status, {"archive": str(archive)}))
        if cleanup.status != "PASS":
            _write_publication_status(run_record_path, {**record, "pullRequest": pr.number, "mergeCommitSha": merge.merge_commit_sha}, "CLEANUP_INCOMPLETE")
            _write_status(primary_record, {**record, "pullRequest": pr.number, "mergeCommitSha": merge.merge_commit_sha}, "CLEANUP_INCOMPLETE")
            return PipelineOutcome("CLEANUP_INCOMPLETE", tuple(stages), _read_json(primary_record), bootstrap=bootstrap, implementer_result=implementer_result, candidate=candidate, validation=validation, review=review, exact_head_gate=exact, publication_gate=gate, pull_request=pr, merge=merge, cleanup=cleanup, violations=tuple(PipelineViolation(item.code, item.message, item.evidence) for item in cleanup.violations))
        local_delete = _run(("git", "branch", "-d", record["featureBranch"]), Path(record["primaryRepository"]))
        if local_delete.returncode != 0:
            _write_status(primary_record, {**record, "pullRequest": pr.number, "mergeCommitSha": merge.merge_commit_sha}, "CLEANUP_INCOMPLETE")
            violation = _violation("LOCAL_BRANCH_DELETE_FAILED", "local feature branch deletion failed", {"stderr": local_delete.stderr})
            return PipelineOutcome("CLEANUP_INCOMPLETE", tuple(stages), _read_json(primary_record), bootstrap=bootstrap, implementer_result=implementer_result, candidate=candidate, validation=validation, review=review, exact_head_gate=exact, publication_gate=gate, pull_request=pr, merge=merge, cleanup=cleanup, violations=(violation,))
        _write_status(primary_record, {**record, "pullRequest": pr.number, "mergeCommitSha": merge.merge_commit_sha}, "COMPLETE")
        return PipelineOutcome("COMPLETE", tuple(stages), {**record, "status": "COMPLETE", "nextStage": "complete"}, bootstrap=bootstrap, implementer_result=implementer_result, candidate=candidate, validation=validation, review=review, exact_head_gate=exact, publication_gate=gate, pull_request=pr, merge=merge, cleanup=cleanup)

    def _cleanup(self, config: ProjectConfig, record: dict[str, Any]) -> WorktreeLifecycleResult:
        return self.lifecycle.remove(
            policy=config.execution_policy,
            request=WorktreeRequest(
                primary_repository_path=Path(record["primaryRepository"]),
                worktree_path=Path(record["featureWorktree"]),
                branch=record["featureBranch"],
                expected_primary_branch=config.default_branch,
                allowed_primary_local_paths=config.allowed_primary_local_paths,
            ),
        )

    def _cleanup_resume_worktree(self, config: ProjectConfig, record: dict[str, Any]) -> WorktreeLifecycleResult:
        if not config.execution_policy.cleanup.autonomous:
            return WorktreeLifecycleResult(
                "remove",
                "BLOCK",
                (
                    WorktreeViolation(
                        "CLEANUP_AUTONOMY_DISABLED",
                        "execution policy does not allow autonomous cleanup",
                        {"autonomous": str(config.execution_policy.cleanup.autonomous)},
                    ),
                ),
                (),
            )

        provider = getattr(self.lifecycle, "provider", None)
        if provider is None:
            return self._cleanup(config, record)

        primary = Path(record["primaryRepository"])
        expected_path = Path(record["featureWorktree"]).resolve()
        expected_branch = str(record["featureBranch"])
        try:
            records = provider.list_worktrees(primary)
        except RepositoryProviderError as exc:
            return WorktreeLifecycleResult(
                "remove",
                "BLOCK",
                (WorktreeViolation(exc.code, exc.message, {"primary_repository_path": str(primary)}),),
                (),
            )

        for worktree in records:
            if worktree.path == expected_path:
                if worktree.branch != expected_branch:
                    return WorktreeLifecycleResult(
                        "remove",
                        "BLOCK",
                        (
                            WorktreeViolation(
                                "WORKTREE_BRANCH_MISMATCH",
                                "worktree branch does not match expected branch",
                                {"expected": expected_branch, "actual": worktree.branch},
                            ),
                        ),
                        (
                            {"key": "worktree_path", "value": str(worktree.path)},
                            {"key": "branch", "value": worktree.branch},
                            {"key": "head", "value": worktree.head},
                        ),
                    )
                return self._cleanup(config, record)

        for worktree in records:
            if worktree.branch == expected_branch:
                return WorktreeLifecycleResult(
                    "remove",
                    "BLOCK",
                    (
                        WorktreeViolation(
                            "WORKTREE_BRANCH_OWNED_BY_ANOTHER_WORKTREE",
                            "expected feature branch is checked out by another worktree",
                            {
                                "expected_worktree_path": str(expected_path),
                                "actual_worktree_path": str(worktree.path),
                                "branch": expected_branch,
                            },
                        ),
                    ),
                    (),
                )

        return WorktreeLifecycleResult(
            "remove",
            "PASS",
            (),
            (
                {"key": "worktree_path", "value": str(expected_path)},
                {"key": "branch", "value": expected_branch},
                {"key": "status", "value": "already_removed"},
            ),
        )

    def _resume_cleanup(self, config: ProjectConfig, run_record_path: Path, record: dict[str, Any], stages: list[PipelineStage]) -> PipelineOutcome:
        canonical_record = _canonical_run_record_path(Path(record["primaryRepository"]), record)
        existing_canonical = _read_json(canonical_record)
        if isinstance(existing_canonical, dict):
            record = {**record, **existing_canonical}
        primary_update_violations = _update_primary_main(Path(record["primaryRepository"]), config.default_branch)
        if primary_update_violations:
            _write_status(canonical_record, record, "CLEANUP_INCOMPLETE")
            return PipelineOutcome("CLEANUP_INCOMPLETE", tuple([*stages, _stage("primary_update", "BLOCKED", {})]), _read_json(canonical_record), violations=primary_update_violations)
        archive = _existing_primary_archive(Path(record["primaryRepository"]), record)
        if archive is None and Path(record["featureWorktree"]).exists():
            archive = _archive_evidence_from_run(Path(record["primaryRepository"]), run_record_path, record)
        elif archive is None:
            archive = _violation("ARCHIVE_EVIDENCE_MISSING", "cleanup resume requires archived evidence when the feature worktree is absent", {"run_record": str(run_record_path)})
        if isinstance(archive, PipelineViolation):
            _write_status(canonical_record, record, "CLEANUP_INCOMPLETE")
            return PipelineOutcome("CLEANUP_INCOMPLETE", tuple([*stages, _stage("archive", "BLOCKED", {})]), _read_json(canonical_record), violations=(archive,))
        stages.append(_stage("archive", "PASS", {"archive": str(archive)}))
        remote_delete = self.publisher.delete_remote_branch(Path(record["primaryRepository"]), record["featureBranch"])
        if remote_delete:
            _write_status(canonical_record, record, "CLEANUP_INCOMPLETE")
            return PipelineOutcome("CLEANUP_INCOMPLETE", tuple([*stages, _stage("remote_branch_delete", "BLOCKED", {})]), _read_json(canonical_record), violations=(remote_delete,))
        cleanup: WorktreeLifecycleResult | None = None
        cleanup = self._cleanup_resume_worktree(config, record)
        stages.append(_stage("cleanup", cleanup.status, {}))
        if cleanup.status != "PASS":
            _write_status(canonical_record, record, "CLEANUP_INCOMPLETE")
            return PipelineOutcome("CLEANUP_INCOMPLETE", tuple(stages), _read_json(canonical_record), cleanup=cleanup, violations=tuple(PipelineViolation(item.code, item.message, item.evidence) for item in cleanup.violations))
        local_delete = _run(("git", "branch", "-d", record["featureBranch"]), Path(record["primaryRepository"]))
        if local_delete.returncode != 0 and "not found" not in (local_delete.stderr + local_delete.stdout).lower():
            _write_status(canonical_record, record, "CLEANUP_INCOMPLETE")
            return PipelineOutcome("CLEANUP_INCOMPLETE", tuple(stages), _read_json(canonical_record), cleanup=cleanup, violations=(_violation("LOCAL_BRANCH_DELETE_FAILED", "local feature branch deletion failed", {"stderr": local_delete.stderr}),))
        _write_status(canonical_record, record, "COMPLETE")
        return PipelineOutcome("COMPLETE", tuple([*stages, _stage("cleanup", "PASS", {})]), _read_json(canonical_record), cleanup=cleanup)

    def _resume_no_changes_cleanup(self, config: ProjectConfig, run_record_path: Path, record: dict[str, Any], stages: list[PipelineStage]) -> PipelineOutcome:
        canonical_record = _canonical_run_record_path(Path(record["primaryRepository"]), record)
        existing_canonical = _read_json(canonical_record)
        if isinstance(existing_canonical, dict):
            record = {**record, **existing_canonical}
        candidate = _candidate_from_mapping(_read_run_artifact(run_record_path, record, "candidate.json")) or CandidatePreparationResult("NO_CHANGES", str(record.get("candidateSha", "")), ())
        violations = _no_change_violations(self.git, Path(record["featureWorktree"]), record, candidate.candidate_sha)
        if Path(record["featureWorktree"]).exists() and violations:
            return PipelineOutcome("NO_CHANGES_CLEANUP_INCOMPLETE", tuple([*stages, _stage("no_changes", "BLOCKED", {})]), record, candidate=candidate, violations=violations)
        archive = Path(str(record.get("noChangeArchive", "")))
        if not archive.exists():
            archive = _archive_no_change(Path(record["primaryRepository"]), record, candidate)
        cleanup: WorktreeLifecycleResult | None = None
        if Path(record["featureWorktree"]).exists():
            cleanup = self._cleanup(config, record)
            stages.append(_stage("cleanup", cleanup.status, {"archive": str(archive)}))
            if cleanup.status != "PASS":
                _write_status(canonical_record, {**record, "noChangeArchive": str(archive)}, "NO_CHANGES_CLEANUP_INCOMPLETE")
                return PipelineOutcome("NO_CHANGES_CLEANUP_INCOMPLETE", tuple(stages), _read_json(canonical_record), candidate=candidate, cleanup=cleanup, violations=tuple(PipelineViolation(item.code, item.message, item.evidence) for item in cleanup.violations))
        local_delete = _run(("git", "branch", "-d", record["featureBranch"]), Path(record["primaryRepository"]))
        if local_delete.returncode != 0 and "not found" not in (local_delete.stderr + local_delete.stdout).lower():
            _write_status(canonical_record, {**record, "noChangeArchive": str(archive)}, "NO_CHANGES_CLEANUP_INCOMPLETE")
            return PipelineOutcome("NO_CHANGES_CLEANUP_INCOMPLETE", tuple(stages), _read_json(canonical_record), candidate=candidate, cleanup=cleanup, violations=(_violation("LOCAL_BRANCH_DELETE_FAILED", "local no-change feature branch deletion failed", {"stderr": local_delete.stderr}),))
        _write_status(canonical_record, {**record, "noChangeArchive": str(archive)}, "NO_CHANGES")
        return PipelineOutcome("NO_CHANGES", tuple([*stages, _stage("cleanup", "PASS", {"archive": str(archive)})]), _read_json(canonical_record), candidate=candidate, cleanup=cleanup)

    def _adopt_validation_recovery_candidate(
        self,
        config: ProjectConfig,
        run_record_path: Path,
        record: dict[str, Any],
        candidate_artifact: Any,
        validation_artifact: Any,
        stages: list[PipelineStage],
    ) -> tuple[dict[str, Any], dict[str, Any]] | PipelineOutcome | None:
        failed_candidate = _candidate_from_mapping(candidate_artifact)
        validation = _validation_from_mapping(validation_artifact) if isinstance(validation_artifact, dict) else None
        if failed_candidate is None or validation is None:
            return None
        required = ("featureWorktree", "featureBranch", "primaryRepository", "authoritativeBaseSha", "runId")
        missing = [key for key in required if not isinstance(record.get(key), str) or not record.get(key)]
        if missing:
            return PipelineOutcome(
                "VALIDATION_FAILED",
                tuple([*stages, _stage("recovery_candidate_adoption", "BLOCKED", {})]),
                record,
                violations=tuple(_violation("RECOVERY_ADOPTION_RECORD_INVALID", "run record is missing required recovery adoption field", {"field": key}) for key in missing),
            )

        worktree = Path(record["featureWorktree"])
        try:
            status = self.git.status(worktree)
            current_head = self.git.current_head(worktree)
            branch = self.git.current_branch(worktree)
        except RepositoryProviderError as exc:
            return PipelineOutcome("VALIDATION_FAILED", tuple([*stages, _stage("recovery_candidate_adoption", "BLOCKED", {})]), record, violations=(_violation(exc.code, exc.message, {"worktree": str(worktree)}),))

        if current_head == failed_candidate.candidate_sha:
            return None
        if branch != record["featureBranch"]:
            return PipelineOutcome(
                "VALIDATION_FAILED",
                tuple([*stages, _stage("recovery_candidate_adoption", "BLOCKED", {"branch": branch})]),
                record,
                violations=(_violation("RECOVERY_ADOPTION_BRANCH_MISMATCH", "validation recovery candidate adoption requires the expected feature branch", {"expected": str(record["featureBranch"]), "actual": branch}),),
            )
        if status.staged or status.dirty_tracked or status.untracked:
            return PipelineOutcome(
                "VALIDATION_FAILED",
                tuple([*stages, _stage("recovery_candidate_adoption", "BLOCKED", {})]),
                record,
                violations=(_violation("RECOVERY_ADOPTION_WORKTREE_DIRTY", "validation recovery candidate adoption requires a clean feature worktree", {"staged": ",".join(status.staged), "dirty": ",".join(status.dirty_tracked), "untracked": ",".join(status.untracked)}),),
            )

        guardian = self.guardian.audit(
            policy=config.execution_policy,
            repository_path=Path(record["primaryRepository"]),
            expected_repository_path=config.primary_repository_path,
            expected_branch=config.default_branch,
            allowed_local_paths=config.allowed_primary_local_paths,
        )
        if guardian.status == "BLOCK":
            return PipelineOutcome(
                "VALIDATION_FAILED",
                tuple([*stages, _stage("recovery_candidate_adoption", "BLOCKED", {})]),
                record,
                violations=tuple(PipelineViolation(f"PRIMARY_{item.code}", item.message, item.evidence) for item in guardian.violations),
            )

        try:
            if not self.git.is_ancestor(worktree, str(record["authoritativeBaseSha"]), current_head):
                return PipelineOutcome(
                    "VALIDATION_FAILED",
                    tuple([*stages, _stage("recovery_candidate_adoption", "BLOCKED", {"current_head": current_head})]),
                    record,
                    violations=(_violation("RECOVERY_ADOPTION_BASE_STALE", "recovery candidate does not descend from recorded authoritative base", {"base": str(record["authoritativeBaseSha"]), "current_head": current_head}),),
                )
            if not self.git.is_ancestor(worktree, failed_candidate.candidate_sha, current_head):
                return PipelineOutcome(
                    "VALIDATION_FAILED",
                    tuple([*stages, _stage("recovery_candidate_adoption", "BLOCKED", {"current_head": current_head})]),
                    record,
                    violations=(_violation("RECOVERY_ADOPTION_LINEAGE_MISMATCH", "recovery candidate does not descend from failed candidate", {"failed_candidate_sha": failed_candidate.candidate_sha, "current_head": current_head}),),
                )
        except RepositoryProviderError as exc:
            return PipelineOutcome("VALIDATION_FAILED", tuple([*stages, _stage("recovery_candidate_adoption", "BLOCKED", {})]), record, violations=(_violation(exc.code, exc.message, {"worktree": str(worktree)}),))

        changed_files = tuple(_git_output(worktree, "diff", "--name-only", f"{failed_candidate.candidate_sha}..{current_head}").splitlines())
        candidate_archive = run_record_path.with_name(f"candidate-before-recovery-adoption-{failed_candidate.candidate_sha[:12]}.json")
        validation_archive = run_record_path.with_name(f"validation-runtime-before-recovery-adoption-{failed_candidate.candidate_sha[:12]}.json")
        _write_json(candidate_archive, candidate_artifact)
        _write_json(validation_archive, validation_artifact)
        adoption = {
            "status": "ADOPTED",
            "runId": str(record["runId"]),
            "previousFailedCandidateSha": failed_candidate.candidate_sha,
            "adoptedCandidateSha": current_head,
            "adoptedChangedFiles": list(changed_files),
            "featureBranch": str(record["featureBranch"]),
            "featureWorktree": str(worktree),
            "authoritativeBaseSha": str(record["authoritativeBaseSha"]),
            "reason": "clean_new_head_on_expected_validation_recovery_worktree",
            "previousValidationStatus": validation.status,
            "previousCandidateArtifact": str(candidate_archive),
            "previousValidationArtifact": str(validation_archive),
        }
        candidate = {"status": "COMMITTED", "candidate_sha": current_head, "changed_files": list(changed_files)}
        _write_json(run_record_path.with_name("recovery-candidate-adoption.json"), adoption)
        _write_json(run_record_path.with_name("candidate.json"), candidate)
        updated = dict(record)
        updated["status"] = "READY_FOR_VALIDATION"
        updated["nextStage"] = "validation"
        updated["recoveryCandidateAdoption"] = adoption
        _clear_resolved_recovery_fields(updated, "READY_FOR_VALIDATION")
        _write_json(run_record_path, updated)
        return updated, candidate

    def _resume_publication(self, config: ProjectConfig, run_record_path: Path, record: dict[str, Any], stages: list[PipelineStage]) -> PipelineOutcome | None:
        candidate = _read_run_artifact(run_record_path, record, "candidate.json")
        validation = _read_run_artifact(run_record_path, record, "validation-runtime.json")
        review = _read_run_artifact(run_record_path, record, "review-runtime.json")
        if not isinstance(candidate, dict) or not isinstance(validation, dict) or not isinstance(review, dict):
            return PipelineOutcome("PUBLICATION_BLOCKED", tuple([*stages, _stage("publication_resume", "BLOCKED", {})]), record, violations=(_violation("PUBLICATION_RESUME_EVIDENCE_MISSING", "publication resume requires candidate, validation, and review evidence", {"run_record": str(run_record_path)}),))
        candidate_result = CandidatePreparationResult("COMMITTED", str(candidate.get("candidate_sha", "")), tuple(str(item) for item in candidate.get("changed_files", [])))
        validation_result = _validation_from_mapping(validation)
        review_result = _review_from_mapping(review)
        if candidate_result.candidate_sha and validation_result.status == "PASS" and review_result.decision == "Approved":
            exact = self.exact_head.verify(repository_path=Path(record["featureWorktree"]), approved_review_sha=review_result.reviewed_sha, validated_sha=validation_result.head_after)
            stages.append(_stage("exact_head", exact.status, {"approved_review_sha": review_result.reviewed_sha, "validated_sha": validation_result.head_after}))
            if exact.status != "MATCH":
                return PipelineOutcome("EXACT_HEAD_BLOCKED", tuple(stages), record, candidate=candidate_result, validation=validation_result, review=review_result, exact_head_gate=exact.to_dict(), violations=tuple(PipelineViolation(item.code, item.message, item.evidence) for item in exact.violations))
            return self._publish(config, run_record_path, record, stages, (), None, candidate_result, validation_result, review_result, exact.to_dict())
        return PipelineOutcome("PUBLICATION_BLOCKED", tuple([*stages, _stage("publication_resume", "BLOCKED", {})]), record, candidate=candidate_result, validation=validation_result, review=review_result, violations=(_violation("PUBLICATION_RESUME_EVIDENCE_INVALID", "publication resume requires a committed candidate, passed validation, and approved review", {"candidate_status": candidate_result.status, "validation_status": validation_result.status, "review_decision": review_result.decision}),))

    def _resume_review(self, config: ProjectConfig, run_record_path: Path, record: dict[str, Any], stages: list[PipelineStage], timeout_ms: int) -> PipelineOutcome:
        candidate_raw = _read_json(run_record_path.with_name("candidate.json"))
        validation_raw = _read_json(run_record_path.with_name("validation-runtime.json"))
        review_raw = _read_json(run_record_path.with_name("review-runtime.json"))
        legacy_no_change = _legacy_no_change_violations(self.git, Path(record["featureWorktree"]), record, candidate_raw, validation_raw)
        if legacy_no_change is not None:
            if legacy_no_change:
                return PipelineOutcome("REVIEW_BLOCKED", tuple([*stages, _stage("no_change_recovery", "BLOCKED", {})]), record, violations=legacy_no_change)
            candidate = _candidate_from_mapping(candidate_raw) or CandidatePreparationResult("NO_CHANGES", str(record.get("authoritativeBaseSha", "")), ())
            return self._finalize_no_changes(config, run_record_path, record, [*stages, _stage("no_change_recovery", "PASS", {"candidate_sha": candidate.candidate_sha})], (), None, CandidatePreparationResult("NO_CHANGES", candidate.candidate_sha, candidate.changed_files))
        resumable = transient_review_blocked_evidence(candidate_raw, validation_raw, review_raw)
        if resumable:
            changes_requested_resume = review_changes_requested_evidence(record, candidate_raw, validation_raw, review_raw, run_record_path)
            if changes_requested_resume:
                return PipelineOutcome("REVIEW_BLOCKED", tuple([*stages, _stage("review_resume", "BLOCKED", {})]), record, violations=resumable)

        candidate = _candidate_from_mapping(candidate_raw)
        validation = _validation_from_mapping(validation_raw) if isinstance(validation_raw, dict) else None
        if candidate is None or validation is None:
            return PipelineOutcome("REVIEW_BLOCKED", tuple([*stages, _stage("review_resume", "BLOCKED", {})]), record, violations=(_violation("REVIEW_RESUME_EVIDENCE_MISSING", "review resume evidence is incomplete", {}),))

        worktree = Path(record["featureWorktree"])
        try:
            status = self.git.status(worktree)
            current_head = self.git.current_head(worktree)
            branch = self.git.current_branch(worktree)
        except RepositoryProviderError as exc:
            return PipelineOutcome("REVIEW_BLOCKED", tuple([*stages, _stage("review_resume", "BLOCKED", {})]), record, candidate=candidate, validation=validation, violations=(_violation(exc.code, exc.message, {"worktree": str(worktree)}),))
        if branch != record["featureBranch"]:
            return PipelineOutcome("REVIEW_BLOCKED", tuple([*stages, _stage("review_resume", "BLOCKED", {"branch": branch})]), record, candidate=candidate, validation=validation, violations=(_violation("REVIEW_RESUME_BRANCH_MISMATCH", "review resume worktree is on a different branch", {"expected": str(record["featureBranch"]), "actual": branch}),))
        if status.staged or status.dirty_tracked or status.untracked:
            return PipelineOutcome("REVIEW_BLOCKED", tuple([*stages, _stage("review_resume", "BLOCKED", {})]), record, candidate=candidate, validation=validation, violations=(_violation("REVIEW_RESUME_WORKTREE_DIRTY", "review resume requires a clean feature worktree", {"staged": ",".join(status.staged), "dirty": ",".join(status.dirty_tracked), "untracked": ",".join(status.untracked)}),))
        if current_head != candidate.candidate_sha:
            changes_requested_resume = review_changes_requested_evidence(record, candidate_raw, validation_raw, review_raw, run_record_path)
            if not changes_requested_resume:
                adoption = self._adopt_review_changes_recovery_candidate(config, run_record_path, record, candidate_raw, validation_raw, review_raw, candidate, validation, current_head, branch, status, stages)
                if isinstance(adoption, PipelineOutcome):
                    return adoption
                stages.append(_stage("review_changes_recovery_adoption", "PASS", {"candidate_sha": str(adoption.get("candidate_sha", ""))}))
                return self.run(config=config, run_record_path=run_record_path, timeout_ms=timeout_ms)
            return PipelineOutcome("REVIEW_BLOCKED", tuple([*stages, _stage("review_resume", "BLOCKED", {"current_head": current_head})]), record, candidate=candidate, validation=validation, violations=(_violation("REVIEW_RESUME_SHA_MISMATCH", "review resume HEAD does not match validated candidate", {"current_head": current_head, "candidate_sha": candidate.candidate_sha}),))

        diff = _git_output(worktree, "diff", "--no-ext-diff", "--no-color", f"{record['authoritativeBaseSha']}..{candidate.candidate_sha}")
        review_scope = f"specs/{record['specNumber']}-{record['featureSlug']}"
        if not (worktree / review_scope).exists():
            review_scope = str(record["featureDescription"])
        review_artifact_snapshot = _review_artifact_snapshot(worktree, review_scope)
        review = self.review.run(
            policy=config.execution_policy,
            request=ReviewRequest(
                repository_path=worktree,
                candidate_sha=candidate.candidate_sha,
                base_sha=record["authoritativeBaseSha"],
                scope=review_scope,
                diff=diff,
            ),
        )
        _write_json(run_record_path.with_name("review-runtime.json"), review.to_dict())
        side_effect_violations = _isolate_review_artifacts(worktree, run_record_path, record, review_scope, candidate.candidate_sha, review_artifact_snapshot)
        if side_effect_violations:
            _write_review_block_status(run_record_path, record, review, candidate, validation, block_violations=side_effect_violations, block_cause="review_side_effect")
            return PipelineOutcome("REVIEW_BLOCKED", tuple(stages), _read_json(run_record_path), candidate=candidate, validation=validation, review=review, violations=side_effect_violations)
        stages.append(_stage("review", review.decision, {"reviewed_sha": review.reviewed_sha, "resumed": "true"}))
        if review.status != "PASS":
            _write_review_block_status(run_record_path, record, review, candidate, validation)
            return PipelineOutcome("REVIEW_BLOCKED", tuple(stages), _read_json(run_record_path), candidate=candidate, validation=validation, review=review, violations=tuple(_from_review(item) for item in review.violations))
        if review.decision == "Changes Requested":
            _write_status(run_record_path, record, "READY_FOR_IMPLEMENTATION")
            return self.run(config=config, run_record_path=run_record_path, timeout_ms=timeout_ms)
        if review.decision != "Approved":
            _write_review_block_status(run_record_path, record, review, candidate, validation, block_violations=(_violation("REVIEW_DECISION_UNAVAILABLE", "review decision was not Approved or Changes Requested", {}),), block_cause="review_decision_unavailable")
            return PipelineOutcome("REVIEW_BLOCKED", tuple(stages), _read_json(run_record_path), candidate=candidate, validation=validation, review=review, violations=(_violation("REVIEW_DECISION_UNAVAILABLE", "review decision was not Approved or Changes Requested", {}),))
        _write_status(run_record_path, record, "REVIEW_APPROVED")
        exact = self.exact_head.verify(repository_path=worktree, approved_review_sha=review.reviewed_sha, validated_sha=validation.head_after)
        stages.append(_stage("exact_head", exact.status, {"approved_review_sha": review.reviewed_sha, "validated_sha": validation.head_after}))
        if exact.status != "MATCH":
            return PipelineOutcome("EXACT_HEAD_BLOCKED", tuple(stages), _read_json(run_record_path), candidate=candidate, validation=validation, review=review, exact_head_gate=exact.to_dict(), violations=tuple(PipelineViolation(item.code, item.message, item.evidence) for item in exact.violations))
        return self._publish(config, run_record_path, record, stages, (), None, candidate, validation, review, exact.to_dict())

    def _adopt_review_changes_recovery_candidate(
        self,
        config: ProjectConfig,
        run_record_path: Path,
        record: dict[str, Any],
        candidate_artifact: Any,
        validation_artifact: Any,
        review_artifact: Any,
        blocked_candidate: CandidatePreparationResult,
        blocked_validation: ValidationResult,
        current_head: str,
        branch: str,
        status: Any,
        stages: list[PipelineStage],
    ) -> dict[str, Any] | PipelineOutcome:
        required = ("featureWorktree", "featureBranch", "primaryRepository", "authoritativeBaseSha", "runId")
        missing = [key for key in required if not isinstance(record.get(key), str) or not record.get(key)]
        if missing:
            return PipelineOutcome(
                "REVIEW_BLOCKED",
                tuple([*stages, _stage("review_changes_recovery_adoption", "BLOCKED", {})]),
                record,
                candidate=blocked_candidate,
                validation=blocked_validation,
                violations=tuple(_violation("REVIEW_RECOVERY_ADOPTION_RECORD_INVALID", "run record is missing required recovery adoption field", {"field": key}) for key in missing),
            )

        worktree = Path(record["featureWorktree"])
        if branch != record["featureBranch"]:
            return PipelineOutcome(
                "REVIEW_BLOCKED",
                tuple([*stages, _stage("review_changes_recovery_adoption", "BLOCKED", {"branch": branch})]),
                record,
                candidate=blocked_candidate,
                validation=blocked_validation,
                violations=(_violation("REVIEW_RECOVERY_ADOPTION_BRANCH_MISMATCH", "review recovery candidate adoption requires the expected feature branch", {"expected": str(record["featureBranch"]), "actual": branch}),),
            )
        if status.staged or status.dirty_tracked or status.untracked:
            return PipelineOutcome(
                "REVIEW_BLOCKED",
                tuple([*stages, _stage("review_changes_recovery_adoption", "BLOCKED", {})]),
                record,
                candidate=blocked_candidate,
                validation=blocked_validation,
                violations=(_violation("REVIEW_RECOVERY_ADOPTION_WORKTREE_DIRTY", "review recovery candidate adoption requires a clean feature worktree", {"staged": ",".join(status.staged), "dirty": ",".join(status.dirty_tracked), "untracked": ",".join(status.untracked)}),),
            )

        guardian = self.guardian.audit(
            policy=config.execution_policy,
            repository_path=Path(record["primaryRepository"]),
            expected_repository_path=config.primary_repository_path,
            expected_branch=config.default_branch,
            allowed_local_paths=config.allowed_primary_local_paths,
        )
        if guardian.status == "BLOCK":
            return PipelineOutcome(
                "REVIEW_BLOCKED",
                tuple([*stages, _stage("review_changes_recovery_adoption", "BLOCKED", {})]),
                record,
                candidate=blocked_candidate,
                validation=blocked_validation,
                violations=tuple(PipelineViolation(f"PRIMARY_{item.code}", item.message, item.evidence) for item in guardian.violations),
            )

        try:
            if not self.git.is_ancestor(worktree, str(record["authoritativeBaseSha"]), current_head):
                return PipelineOutcome(
                    "REVIEW_BLOCKED",
                    tuple([*stages, _stage("review_changes_recovery_adoption", "BLOCKED", {"current_head": current_head})]),
                    record,
                    candidate=blocked_candidate,
                    validation=blocked_validation,
                    violations=(_violation("REVIEW_RECOVERY_ADOPTION_BASE_STALE", "review recovery candidate does not descend from recorded authoritative base", {"base": str(record["authoritativeBaseSha"]), "current_head": current_head}),),
                )
            if not self.git.is_ancestor(worktree, blocked_candidate.candidate_sha, current_head):
                return PipelineOutcome(
                    "REVIEW_BLOCKED",
                    tuple([*stages, _stage("review_changes_recovery_adoption", "BLOCKED", {"current_head": current_head})]),
                    record,
                    candidate=blocked_candidate,
                    validation=blocked_validation,
                    violations=(_violation("REVIEW_RECOVERY_ADOPTION_LINEAGE_MISMATCH", "review recovery candidate does not descend from reviewed candidate", {"reviewed_candidate_sha": blocked_candidate.candidate_sha, "current_head": current_head}),),
                )
        except RepositoryProviderError as exc:
            return PipelineOutcome("REVIEW_BLOCKED", tuple([*stages, _stage("review_changes_recovery_adoption", "BLOCKED", {})]), record, candidate=blocked_candidate, validation=blocked_validation, violations=(_violation(exc.code, exc.message, {"worktree": str(worktree)}),))

        changed_files = tuple(_git_output(worktree, "diff", "--name-only", f"{blocked_candidate.candidate_sha}..{current_head}").splitlines())
        candidate_archive = run_record_path.with_name(f"candidate-before-review-recovery-adoption-{blocked_candidate.candidate_sha[:12]}.json")
        validation_archive = run_record_path.with_name(f"validation-runtime-before-review-recovery-adoption-{blocked_candidate.candidate_sha[:12]}.json")
        review_archive = run_record_path.with_name(f"review-runtime-before-review-recovery-adoption-{blocked_candidate.candidate_sha[:12]}.json")
        _write_json(candidate_archive, candidate_artifact)
        _write_json(validation_archive, validation_artifact)
        _write_json(review_archive, review_artifact)
        adoption = {
            "status": "ADOPTED",
            "runId": str(record["runId"]),
            "previousReviewedCandidateSha": blocked_candidate.candidate_sha,
            "previousValidatedSha": blocked_validation.head_after,
            "adoptedCandidateSha": current_head,
            "adoptedChangedFiles": list(changed_files),
            "featureBranch": str(record["featureBranch"]),
            "featureWorktree": str(worktree),
            "authoritativeBaseSha": str(record["authoritativeBaseSha"]),
            "reason": "clean_new_head_after_changes_requested_review",
            "previousCandidateArtifact": str(candidate_archive),
            "previousValidationArtifact": str(validation_archive),
            "previousReviewArtifact": str(review_archive),
        }
        candidate = {"status": "COMMITTED", "candidate_sha": current_head, "changed_files": list(changed_files)}
        _write_json(run_record_path.with_name("review-changes-recovery-adoption.json"), adoption)
        _write_json(run_record_path.with_name("candidate.json"), candidate)
        updated = dict(record)
        updated["status"] = "READY_FOR_VALIDATION"
        updated["nextStage"] = "validation"
        updated["reviewChangesRecoveryAdoption"] = adoption
        _clear_resolved_recovery_fields(updated, "READY_FOR_VALIDATION")
        _write_json(run_record_path, updated)
        return candidate


def _run(args: tuple[str, ...], cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(args, cwd=cwd, shell=False, capture_output=True, text=True, encoding="utf-8", errors="replace")


def _git_output(repo: Path, *args: str) -> str:
    completed = _run(("git", *args), repo)
    return completed.stdout if completed.returncode == 0 else ""


def _write_status(path: Path, record: dict[str, Any], status: str) -> None:
    updated = dict(record)
    updated["status"] = status
    updated["nextStage"] = _next_stage(status)
    _clear_resolved_recovery_fields(updated, status)
    _write_json(path, updated)


def _write_validation_failure_status(path: Path, record: dict[str, Any], candidate: CandidatePreparationResult, validation: ValidationResult) -> None:
    updated = dict(record)
    updated["status"] = "VALIDATION_FAILED"
    updated["nextStage"] = "implementation_recovery"
    updated["validationFailure"] = _validation_failure_payload(path, candidate, validation)
    _write_json(path, updated)


def _validation_failure_payload(path: Path, candidate: CandidatePreparationResult, validation: ValidationResult) -> dict[str, Any]:
    failed_commands = [
        {
            "command": command.command,
            "exitCode": "" if command.exit_code is None else str(command.exit_code),
            "reasonCode": "VALIDATION_COMMAND_TIMED_OUT" if command.timed_out else "VALIDATION_COMMAND_FAILED",
            "stdout": _bounded(command.stdout),
            "stderr": _bounded(command.stderr),
            "timedOut": str(command.timed_out).lower(),
        }
        for command in validation.commands
        if command.timed_out or command.exit_code != 0
    ]
    if validation.head_before != validation.head_after:
        failed_commands.append(
            {
                "command": "HEAD",
                "exitCode": "",
                "reasonCode": "VALIDATION_HEAD_DRIFT",
                "stdout": "",
                "stderr": f"head_before={validation.head_before} head_after={validation.head_after}",
            }
        )
    return {
        "candidateSha": candidate.candidate_sha,
        "headBefore": validation.head_before,
        "headAfter": validation.head_after,
        "status": validation.status,
        "reasonCodes": [violation.code for violation in validation.violations],
        "failedCommands": failed_commands,
        "artifact": str(path.with_name("validation-runtime.json")),
        "recoveryStage": "implementation_recovery",
    }


def _ensure_validation_failure_evidence(path: Path, record: dict[str, Any], candidate: Any, validation: Any) -> None:
    candidate_result = _candidate_from_mapping(candidate)
    validation_result = _validation_from_mapping(validation) if isinstance(validation, dict) else None
    if candidate_result is None or validation_result is None:
        return
    current = record.get("validationFailure")
    if isinstance(current, dict) and current.get("candidateSha") == candidate_result.candidate_sha and current.get("failedCommands"):
        return
    updated = dict(record)
    updated["status"] = "VALIDATION_FAILED"
    updated["nextStage"] = "implementation_recovery"
    updated["validationFailure"] = _validation_failure_payload(path, candidate_result, validation_result)
    _write_json(path, updated)


def _validation_recovery_attempt_count(record: dict[str, Any]) -> int:
    attempts = record.get("validationRecoveryAttempts")
    return len(attempts) if isinstance(attempts, list) else 0


def _append_validation_recovery_attempt(path: Path, record: dict[str, Any], candidate: CandidatePreparationResult, validation: ValidationResult, round_number: int, max_rounds: int) -> None:
    updated = dict(record)
    attempts_raw = updated.get("validationRecoveryAttempts")
    attempts = list(attempts_raw) if isinstance(attempts_raw, list) else []
    validation_artifact = path.with_name("validation-runtime.json")
    archived_artifact = path.with_name(f"validation-recovery-round-{round_number}-failed-validation.json")
    artifact_path = validation_artifact
    if validation_artifact.exists():
        shutil.copyfile(validation_artifact, archived_artifact)
        artifact_path = archived_artifact
    attempts.append(
        {
            "round": round_number,
            "maxRounds": max_rounds,
            "status": "RECOVERY_IMPLEMENTER_PENDING",
            "failedCandidateSha": candidate.candidate_sha,
            "failedValidationStatus": validation.status,
            "failedValidationArtifact": str(artifact_path),
            "failedCommands": _validation_failure_payload(path, candidate, validation)["failedCommands"],
            "failedCommandCodes": [item.code for item in validation.violations],
        }
    )
    updated["validationRecoveryAttempts"] = attempts
    updated.pop("validationRecoveryBlock", None)
    _write_json(path, updated)


def _update_validation_recovery_attempt(path: Path, round_number: int, fields: dict[str, Any]) -> None:
    record = _read_json(path)
    if not isinstance(record, dict):
        return
    attempts_raw = record.get("validationRecoveryAttempts")
    if not isinstance(attempts_raw, list):
        return
    attempts: list[Any] = []
    for attempt in attempts_raw:
        if isinstance(attempt, dict) and attempt.get("round") == round_number:
            attempts.append({**attempt, **fields})
        else:
            attempts.append(attempt)
    record["validationRecoveryAttempts"] = attempts
    _write_json(path, record)


def _record_validation_recovery_candidate(path: Path, record: dict[str, Any], candidate: CandidatePreparationResult) -> None:
    attempts_raw = record.get("validationRecoveryAttempts")
    if not isinstance(attempts_raw, list) or not attempts_raw:
        return
    latest = attempts_raw[-1]
    if not isinstance(latest, dict):
        return
    if latest.get("recoveryCandidateSha") or latest.get("failedCandidateSha") == candidate.candidate_sha:
        return
    _update_validation_recovery_attempt(
        path,
        int(latest.get("round", len(attempts_raw))),
        {
            "status": "RECOVERY_CANDIDATE_RECORDED",
            "recoveryCandidateSha": candidate.candidate_sha,
            "recoveryChangedFiles": list(candidate.changed_files),
        },
    )


def _write_validation_recovery_block_status(path: Path, record: dict[str, Any], violation: PipelineViolation) -> None:
    updated = dict(record)
    updated["status"] = "VALIDATION_FAILED"
    updated["nextStage"] = "human_intervention"
    updated["validationRecoveryBlock"] = {
        "status": "BLOCKED",
        "reasonCode": violation.code,
        "message": violation.message,
        "evidence": violation.evidence,
    }
    _write_json(path, updated)


def _validation_recovery_block_violation(record: dict[str, Any]) -> PipelineViolation | None:
    block = record.get("validationRecoveryBlock")
    if not isinstance(block, dict):
        return None
    if str(block.get("status", "")) != "BLOCKED":
        return None
    evidence_raw = block.get("evidence")
    evidence = {str(key): str(value) for key, value in evidence_raw.items()} if isinstance(evidence_raw, dict) else {}
    return _violation(str(block.get("reasonCode", "VALIDATION_RECOVERY_BLOCKED")), str(block.get("message", "validation recovery is blocked")), evidence)


def _no_change_adjudication_attempt_count(record: dict[str, Any]) -> int:
    attempts = record.get("noChangeAdjudicationAttempts")
    return len(attempts) if isinstance(attempts, list) else 0


def _append_no_change_adjudication(path: Path, record: dict[str, Any], candidate: CandidatePreparationResult, verification: NoChangeVerificationResult, round_number: int, max_rounds: int) -> None:
    updated = dict(record)
    attempts_raw = updated.get("noChangeAdjudicationAttempts")
    attempts = list(attempts_raw) if isinstance(attempts_raw, list) else []
    attempts.append(
        {
            "round": round_number,
            "maxRounds": max_rounds,
            "candidateSha": candidate.candidate_sha,
            "baseSha": str(record.get("authoritativeBaseSha", "")),
            "implementerStatus": candidate.status,
            "verifier": str(record.get("reviewer", "")),
            "verifierDecision": verification.decision,
            "verifierStatus": verification.status,
            "verifierSha": verification.verified_sha,
            "verifierStdout": _bounded(verification.stdout),
            "verifierStderr": _bounded(verification.stderr),
            "verifierViolationCodes": [item.code for item in verification.violations],
        }
    )
    updated["noChangeAdjudicationAttempts"] = attempts
    updated.pop("noChangeAdjudicationBlock", None)
    _write_json(path, updated)


def _update_no_change_adjudication(path: Path, round_number: int, fields: dict[str, Any]) -> None:
    record = _read_json(path)
    if not isinstance(record, dict):
        return
    attempts_raw = record.get("noChangeAdjudicationAttempts")
    if not isinstance(attempts_raw, list):
        return
    attempts: list[Any] = []
    for attempt in attempts_raw:
        if isinstance(attempt, dict) and attempt.get("round") == round_number:
            attempts.append({**attempt, **fields})
        else:
            attempts.append(attempt)
    record["noChangeAdjudicationAttempts"] = attempts
    _write_json(path, record)


def _write_no_change_recovery_status(path: Path, record: dict[str, Any], verification: NoChangeVerificationResult) -> None:
    updated = dict(record)
    updated["status"] = "READY_FOR_IMPLEMENTATION"
    updated["nextStage"] = "no_change_recovery"
    updated["noChangeRecovery"] = {
        "status": "NO_CHANGES_REJECTED_FEATURE_MISSING",
        "verifierDecision": verification.decision,
        "verifierOutput": _bounded(verification.stdout),
        "verifierSha": verification.verified_sha,
    }
    _write_json(path, updated)


def _write_no_change_adjudication_block(path: Path, record: dict[str, Any], violation: PipelineViolation) -> None:
    updated = dict(record)
    updated["status"] = "NO_CHANGES_AMBIGUOUS"
    updated["nextStage"] = "human_intervention"
    updated["noChangeAdjudicationBlock"] = {
        "status": "BLOCKED",
        "reasonCode": violation.code,
        "message": violation.message,
        "evidence": violation.evidence,
    }
    _write_json(path, updated)


def _write_publication_status(path: Path, record: dict[str, Any], status: str) -> None:
    updated = dict(record)
    updated["status"] = status
    updated["nextStage"] = _next_stage(status)
    _clear_resolved_recovery_fields(updated, status)
    _write_json(path, updated)


def _clear_resolved_recovery_fields(record: dict[str, Any], status: str) -> None:
    if status != "VALIDATION_FAILED":
        record.pop("validationFailure", None)
        record.pop("validationRecoveryBlock", None)
    if status not in {"NO_CHANGES_AMBIGUOUS", "READY_FOR_IMPLEMENTATION"}:
        record.pop("noChangeRecovery", None)
        record.pop("noChangeAdjudicationBlock", None)
    if status != "REVIEW_BLOCKED":
        record.pop("reviewBlock", None)


def _record_with_pr(record: dict[str, Any], pr: PullRequestInfo, remote_sha: str) -> dict[str, Any]:
    return {
        **record,
        "remoteBranchHeadSha": remote_sha,
        "pullRequest": pr.number,
        "pullRequestUrl": pr.url,
        "prBaseBranch": pr.base_branch,
        "prHeadBranch": pr.head_branch,
        "prHeadSha": pr.head_sha,
        "prMergeable": str(pr.mergeable).lower(),
        "prDraft": str(pr.draft).lower(),
        "prBaseSha": pr.base_sha,
        "prMergeStateStatus": pr.merge_state_status,
    }


def _refresh_pr_for_gate(
    publisher: PublicationProvider,
    repo: Path,
    pr: PullRequestInfo,
    intended_base: str,
    intended_base_sha: str,
    intended_head: str,
    candidate_sha: str,
) -> PullRequestInfo | PipelineViolation:
    last = pr
    for _attempt in range(PR_REFRESH_ATTEMPTS):
        refreshed = publisher.refresh_pr(repo, pr.number)
        if isinstance(refreshed, PipelineViolation):
            return refreshed
        last = refreshed
        if refreshed.head_sha != candidate_sha:
            return _violation("PR_HEAD_SHA_MISMATCH", "refreshed PR head does not match candidate", {"expected": candidate_sha, "actual": refreshed.head_sha})
        if refreshed.head_branch != intended_head:
            return _violation("PR_HEAD_MISMATCH", "refreshed PR head branch does not match intended branch", {"expected": intended_head, "actual": refreshed.head_branch})
        if refreshed.base_branch != intended_base:
            return _violation("PR_BASE_MISMATCH", "refreshed PR base branch does not match intended branch", {"expected": intended_base, "actual": refreshed.base_branch})
        if refreshed.base_sha and refreshed.base_sha != intended_base_sha:
            return _violation("PR_BASE_SHA_MISMATCH", "refreshed PR base SHA does not match authoritative base", {"expected": intended_base_sha, "actual": refreshed.base_sha})
        if refreshed.draft:
            return _violation("PR_STILL_DRAFT", "PR remains draft after ready transition", {"number": refreshed.number})
        if refreshed.mergeable:
            return refreshed
        if _mergeability_is_final_block(refreshed):
            return refreshed
    return _violation("PR_MERGEABILITY_UNRESOLVED", "PR mergeability did not resolve after bounded refresh", {"number": last.number, "merge_state_status": last.merge_state_status})


def _mergeability_is_final_block(pr: PullRequestInfo) -> bool:
    status = pr.merge_state_status.upper()
    return status in {"DIRTY", "BEHIND", "BLOCKED", "UNSTABLE", "HAS_HOOKS"}


def _review_artifact_snapshot(worktree: Path, scope: str) -> dict[Path, str]:
    candidates = _review_artifact_candidates(worktree, scope)
    snapshot: dict[Path, str] = {}
    for path in candidates:
        if path.exists():
            try:
                snapshot[path] = _git_output(worktree, "ls-files", "--error-unmatch", _relative_to_worktree(worktree, path))
            except ValueError:
                snapshot[path] = ""
    return snapshot


def _isolate_review_artifacts(
    worktree: Path,
    run_record_path: Path,
    record: dict[str, Any],
    scope: str,
    candidate_sha: str,
    before: dict[Path, str],
) -> tuple[PipelineViolation, ...]:
    violations: list[PipelineViolation] = []
    archived: list[dict[str, str]] = []
    for path in _review_artifact_candidates(worktree, scope):
        if path in before or not path.exists():
            continue
        try:
            relative = _relative_to_worktree(worktree, path)
        except ValueError:
            violations.append(_violation("REVIEW_ARTIFACT_PATH_INVALID", "review artifact path is outside worktree", {"path": str(path)}))
            continue
        if _git_output(worktree, "ls-files", "--others", "--exclude-standard", "--", relative).strip() != relative:
            violations.append(_violation("REVIEW_SIDE_EFFECT_UNEXPECTED", "review produced an unexpected tracked or ignored side effect", {"path": relative}))
            continue
        archive_dir = run_record_path.parent / "review-artifacts"
        archive_dir.mkdir(parents=True, exist_ok=True)
        archive = archive_dir / f"{path.stem}-{candidate_sha[:12]}{path.suffix}"
        archive.write_text(path.read_text(encoding="utf-8", errors="replace"), encoding="utf-8")
        path.unlink()
        archived.append({"source": relative, "archive": str(archive), "candidateSha": candidate_sha})
    dirty = _run(("git", "status", "--short"), worktree)
    remaining = [line for line in dirty.stdout.splitlines() if line.strip() and ".agent-workflow/" not in line.replace("\\", "/")]
    if remaining:
        violations.append(_violation("REVIEW_SIDE_EFFECT_DIRTY_WORKTREE", "review left product worktree dirty", {"status": "\n".join(remaining)}))
    if archived:
        _write_json(run_record_path.with_name("review-generated-artifacts.json"), {"runId": str(record.get("runId", "")), "artifacts": archived})
    return tuple(violations)


def _review_artifact_candidates(worktree: Path, scope: str) -> tuple[Path, ...]:
    if not _is_path_like_scope(scope):
        return ()
    scope_path = (worktree / scope).resolve()
    try:
        scope_path.relative_to(worktree.resolve())
    except ValueError:
        return ()
    return (scope_path / "review.md",)


def _is_path_like_scope(scope: str) -> bool:
    return "/" in scope or "\\" in scope or scope.startswith("specs") or scope.startswith(".")


def _relative_to_worktree(worktree: Path, path: Path) -> str:
    return str(path.resolve().relative_to(worktree.resolve())).replace("\\", "/")


def _read_run_artifact(run_record_path: Path, record: dict[str, Any], filename: str) -> Any:
    primary = run_record_path.with_name(filename)
    value = _read_json(primary)
    if value is not None:
        return value
    feature = Path(str(record.get("featureWorktree", ""))) / ".agent-workflow" / "runs" / f"{record.get('specNumber', '')}-{record.get('featureSlug', '')}" / filename
    if feature == primary:
        return None
    return _read_json(feature)


def _write_review_block_status(
    path: Path,
    record: dict[str, Any],
    review: ReviewResult,
    candidate: CandidatePreparationResult,
    validation: ValidationResult,
    *,
    block_violations: tuple[PipelineViolation, ...] = (),
    block_cause: str = "",
) -> None:
    transient = _is_transient_review_block(review)
    reason_code = _review_block_reason_code(review, block_violations)
    changes_requested = reason_code == "REVIEW_CHANGES_REQUESTED"
    reason_codes = [violation.code for violation in block_violations] if block_violations else [violation.code for violation in review.violations]
    durable_cause = block_cause or ("review_decision" if changes_requested else "review_runtime")
    updated = dict(record)
    updated["status"] = "REVIEW_BLOCKED"
    updated["nextStage"] = "review" if transient else "implementation_recovery" if changes_requested else "recovery"
    updated["reviewBlock"] = {
        "status": review.status,
        "decision": review.decision,
        "reasonCode": reason_code,
        "reasonCodes": reason_codes,
        "blockCause": durable_cause,
        "transient": transient,
        "resumeStage": "review" if transient else "implementation_recovery" if changes_requested else "",
        "reviewer": str(record.get("reviewer", "")),
        "candidateSha": candidate.candidate_sha,
        "validatedSha": validation.head_after,
        "baseSha": str(record.get("authoritativeBaseSha", "")),
        "reviewedSha": review.reviewed_sha,
        "exitCode": review.exit_code,
        "timedOut": reason_code == "REVIEWER_TIMED_OUT",
    }
    _write_json(path, updated)


def _next_stage(status: str) -> str:
    return {
        "READY_FOR_IMPLEMENTATION": "implementation_handoff",
        "READY_FOR_VALIDATION": "validation",
        "VALIDATION_FAILED": "implementation_recovery",
        "READY_FOR_REVIEW": "review",
        "REVIEW_APPROVED": "publication",
        "REVIEW_CHANGES_REQUESTED": "implementation_recovery",
        "READY_FOR_PUBLICATION": "publication",
        "PR_CREATED": "publication",
        "PR_READY": "publication",
        "MERGED": "cleanup",
        "CLEANUP_INCOMPLETE": "cleanup",
        "COMPLETE": "complete",
        "NO_CHANGES_CLEANUP_INCOMPLETE": "cleanup",
        "NO_CHANGES": "complete",
    }.get(status, "recovery")


def _no_change_violations(git: GitRepositoryProvider, worktree: Path, record: dict[str, Any], candidate_sha: str) -> tuple[PipelineViolation, ...]:
    violations: list[PipelineViolation] = []
    base_sha = str(record.get("authoritativeBaseSha", ""))
    if not candidate_sha or not base_sha:
        return (_violation("NO_CHANGE_SHA_MISSING", "no-change detection requires candidate and base SHA", {"candidate_sha": candidate_sha, "base_sha": base_sha}),)
    try:
        status = git.status(worktree)
        current_head = git.current_head(worktree)
        branch = git.current_branch(worktree)
    except RepositoryProviderError as exc:
        return (_violation(exc.code, exc.message, {"worktree": str(worktree)}),)
    if branch != str(record.get("featureBranch", "")):
        violations.append(_violation("NO_CHANGE_BRANCH_MISMATCH", "no-change cleanup requires the recorded feature branch", {"expected": str(record.get("featureBranch", "")), "actual": branch}))
    if current_head != candidate_sha:
        violations.append(_violation("NO_CHANGE_HEAD_MISMATCH", "no-change cleanup requires HEAD to match candidate", {"current_head": current_head, "candidate_sha": candidate_sha}))
    if status.staged or status.dirty_tracked or status.untracked:
        violations.append(_violation("NO_CHANGE_WORKTREE_DIRTY", "no-change cleanup requires a clean feature worktree", {"staged": ",".join(status.staged), "dirty": ",".join(status.dirty_tracked), "untracked": ",".join(status.untracked)}))
    if candidate_sha != base_sha:
        violations.append(_violation("NO_CHANGE_CANDIDATE_DIFFERS_FROM_BASE", "candidate SHA differs from authoritative base", {"candidate_sha": candidate_sha, "base_sha": base_sha}))
    ahead = _run(("git", "rev-list", "--count", f"{base_sha}..{candidate_sha}"), worktree)
    if ahead.returncode != 0:
        violations.append(_violation("NO_CHANGE_REV_LIST_FAILED", "no-change rev-list proof failed", {"stderr": ahead.stderr}))
    elif ahead.stdout.strip() != "0":
        violations.append(_violation("NO_CHANGE_COMMITS_AHEAD", "candidate has commits ahead of authoritative base", {"count": ahead.stdout.strip()}))
    diff = _run(("git", "diff", "--quiet", f"{base_sha}..{candidate_sha}"), worktree)
    if diff.returncode not in {0, 1}:
        violations.append(_violation("NO_CHANGE_DIFF_FAILED", "no-change diff proof failed", {"stderr": diff.stderr}))
    elif diff.returncode == 1:
        violations.append(_violation("NO_CHANGE_DIFF_NOT_EMPTY", "candidate diff against authoritative base is not empty", {}))
    return tuple(violations)


def _legacy_no_change_violations(git: GitRepositoryProvider, worktree: Path, record: dict[str, Any], candidate_raw: Any, validation_raw: Any) -> tuple[PipelineViolation, ...] | None:
    if str(record.get("status", "")) != "REVIEW_BLOCKED":
        return None
    candidate = _candidate_from_mapping(candidate_raw)
    if candidate is None:
        return None
    base_sha = str(record.get("authoritativeBaseSha", ""))
    if candidate.candidate_sha != base_sha:
        return None
    if isinstance(validation_raw, dict):
        validation = _validation_from_mapping(validation_raw)
        if validation.head_before != candidate.candidate_sha or validation.head_after != candidate.candidate_sha:
            return (_violation("NO_CHANGE_VALIDATION_SHA_MISMATCH", "legacy no-change validation evidence does not match candidate", {"candidate_sha": candidate.candidate_sha, "head_before": validation.head_before, "head_after": validation.head_after}),)
    return _no_change_violations(git, worktree, record, candidate.candidate_sha)


def _archive_no_change(primary: Path, record: dict[str, Any], candidate: CandidatePreparationResult) -> Path:
    archive = primary / ".agent-workflow" / "runs" / f"{record['specNumber']}-{record['featureSlug']}" / "ados-no-change-evidence.json"
    archive.parent.mkdir(parents=True, exist_ok=True)
    _write_json(
        archive,
        {
            "spec": f"{record['specNumber']}-{record['featureSlug']}",
            "run_id": str(record.get("runId", "")),
            "status": "NO_CHANGES",
            "candidate_sha": candidate.candidate_sha,
            "authoritative_base_sha": str(record.get("authoritativeBaseSha", "")),
            "changed_files": list(candidate.changed_files),
            "publication": "not_started",
            "merge_commit": "",
        },
    )
    return archive


def _archive_evidence(primary: Path, record: dict[str, Any], candidate: CandidatePreparationResult, validation: ValidationResult, review: ReviewResult, pr: PullRequestInfo, merge: MergeResult) -> Path:
    archive = primary / ".agent-workflow" / "runs" / f"{record['specNumber']}-{record['featureSlug']}" / "ados-review-evidence.json"
    archive.parent.mkdir(parents=True, exist_ok=True)
    _write_json(
        archive,
        {
            "spec": f"{record['specNumber']}-{record['featureSlug']}",
            "candidate_sha": candidate.candidate_sha,
            "validated_sha": validation.head_after,
            "approved_review_sha": review.reviewed_sha,
            "claude_decision": review.decision,
            "validation": validation.to_dict(),
            "review": review.to_dict(),
            "pull_request": pr.number,
            "pr": {"number": pr.number, "head_sha": pr.head_sha, "merge_commit_sha": merge.merge_commit_sha},
            "merge_commit": merge.merge_commit_sha,
        },
    )
    generated = Path(str(record.get("featureWorktree", ""))) / ".agent-workflow" / "runs" / f"{record['specNumber']}-{record['featureSlug']}" / "review-generated-artifacts.json"
    if generated.exists():
        try:
            generated_payload = json.loads(generated.read_text(encoding="utf-8-sig"))
        except json.JSONDecodeError:
            _write_json(archive.with_name("review-generated-artifacts.json"), {"source": str(generated), "status": "invalid_json"})
        else:
            _write_json(archive.with_name("review-generated-artifacts.json"), _archive_generated_review_artifacts(archive.parent, generated_payload))
    return archive


def _archive_evidence_from_run(primary: Path, run_record_path: Path, record: dict[str, Any]) -> Path | PipelineViolation:
    candidate_raw = _read_run_artifact(run_record_path, record, "candidate.json")
    validation_raw = _read_run_artifact(run_record_path, record, "validation-runtime.json")
    review_raw = _read_run_artifact(run_record_path, record, "review-runtime.json")
    candidate = _candidate_from_mapping(candidate_raw)
    if candidate is None or not isinstance(validation_raw, dict) or not isinstance(review_raw, dict):
        return _violation("ARCHIVE_EVIDENCE_MISSING", "cleanup resume requires candidate, validation, and review evidence before worktree removal", {"run_record": str(run_record_path)})
    validation = _validation_from_mapping(validation_raw)
    review = _review_from_mapping(review_raw)
    merge_sha = str(record.get("mergeCommitSha", ""))
    pr_number = str(record.get("pullRequest", ""))
    if not merge_sha or not pr_number:
        return _violation("ARCHIVE_PUBLICATION_EVIDENCE_MISSING", "cleanup resume requires PR and merge evidence before worktree removal", {"pull_request": pr_number, "merge_commit": merge_sha})
    pr = PullRequestInfo(
        number=pr_number,
        url=str(record.get("pullRequestUrl", "")),
        base_branch=str(record.get("prBaseBranch", "")),
        head_branch=str(record.get("prHeadBranch", "")) or str(record.get("featureBranch", "")),
        head_sha=str(record.get("prHeadSha", "")) or str(record.get("remoteBranchHeadSha", "")) or candidate.candidate_sha,
        mergeable=str(record.get("prMergeable", "")).lower() == "true",
        draft=str(record.get("prDraft", "")).lower() == "true",
        base_sha=str(record.get("prBaseSha", "")),
        merge_state_status=str(record.get("prMergeStateStatus", "")),
    )
    return _archive_evidence(primary, record, candidate, validation, review, pr, MergeResult("MERGED", merge_sha))


def _existing_primary_archive(primary: Path, record: dict[str, Any]) -> Path | None:
    archive = primary / ".agent-workflow" / "runs" / f"{record['specNumber']}-{record['featureSlug']}" / "ados-review-evidence.json"
    return archive if archive.exists() else None


def _archive_generated_review_artifacts(primary_run_dir: Path, payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        return {"status": "invalid_payload"}
    artifacts = payload.get("artifacts", [])
    if not isinstance(artifacts, list):
        return {"status": "invalid_payload"}
    archive_dir = primary_run_dir / "review-artifacts"
    archive_dir.mkdir(parents=True, exist_ok=True)
    archived: list[dict[str, str]] = []
    for index, artifact in enumerate(artifacts):
        if not isinstance(artifact, dict):
            continue
        source_archive = Path(str(artifact.get("archive", "")))
        if not source_archive.exists() or not source_archive.is_file():
            archived.append({**{str(k): str(v) for k, v in artifact.items()}, "status": "missing_source"})
            continue
        target = archive_dir / f"{index:03d}-{source_archive.name}"
        target.write_text(source_archive.read_text(encoding="utf-8", errors="replace"), encoding="utf-8")
        updated = {str(k): str(v) for k, v in artifact.items()}
        updated["archive"] = str(target)
        updated["status"] = "archived"
        archived.append(updated)
    return {**payload, "artifacts": archived}


def _archive_run_record(primary: Path, record: dict[str, Any], status: str) -> Path:
    path = _canonical_run_record_path(primary, record)
    _write_status(path, record, status)
    return path


def _canonical_run_record_path(primary: Path, record: dict[str, Any]) -> Path:
    return primary / ".agent-workflow" / "runs" / f"{record['specNumber']}-{record['featureSlug']}" / "ados-run.json"


def _update_primary_main(primary: Path, branch: str) -> tuple[PipelineViolation, ...]:
    fetch = _run(("git", "fetch", "origin", "--prune", "--quiet"), primary)
    if fetch.returncode != 0:
        return (_violation("PRIMARY_FETCH_FAILED", "primary fetch failed after merge", {"stderr": fetch.stderr}),)
    checkout = _run(("git", "checkout", branch), primary)
    if checkout.returncode != 0:
        return (_violation("PRIMARY_CHECKOUT_FAILED", "primary branch checkout failed after merge", {"stderr": checkout.stderr, "branch": branch}),)
    merge = _run(("git", "merge", "--ff-only", f"origin/{branch}"), primary)
    if merge.returncode != 0:
        return (_violation("PRIMARY_FF_UPDATE_FAILED", "primary main fast-forward failed after merge", {"stderr": merge.stderr, "branch": branch}),)
    return ()


def _is_clean(git: GitRepositoryProvider, repo: Path) -> bool:
    try:
        status = git.status(repo)
    except RepositoryProviderError:
        return False
    return not (status.staged or status.dirty_tracked or status.untracked)


def _is_github_origin(git: GitRepositoryProvider, repo: Path) -> bool:
    try:
        return "github.com" in git.origin_url(repo).lower()
    except RepositoryProviderError:
        return False


def _already_deleted(output: str) -> bool:
    normalized = output.lower()
    return "remote ref does not exist" in normalized or "unable to delete" in normalized and "not found" in normalized


def _validation_from_mapping(raw: dict[str, Any]) -> ValidationResult:
    commands = tuple(
        ValidationCommandResult(
            command=str(item.get("command", "")),
            exit_code=_optional_int(item.get("exit_code")),
            stdout=str(item.get("stdout", "")),
            stderr=str(item.get("stderr", "")),
            timed_out=bool(item.get("timed_out", item.get("timedOut", False))),
        )
        for item in raw.get("commands", [])
        if isinstance(item, dict)
    )
    violations = tuple(
        ValidationViolation(str(item.get("code", "")), str(item.get("message", "")), {str(key): str(value) for key, value in item.get("evidence", {}).items()})
        for item in raw.get("violations", [])
        if isinstance(item, dict)
    )
    return ValidationResult(str(raw.get("status", "")), str(raw.get("head_before", "")), str(raw.get("head_after", "")), commands, violations)


def _optional_int(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _candidate_from_mapping(raw: Any) -> CandidatePreparationResult | None:
    if not isinstance(raw, dict):
        return None
    candidate_sha = str(raw.get("candidate_sha") or raw.get("candidateSha") or "")
    changed_files = raw.get("changed_files", raw.get("changedFiles", []))
    if not isinstance(changed_files, list):
        changed_files = []
    if not candidate_sha:
        return None
    return CandidatePreparationResult(str(raw.get("status", "")), candidate_sha, tuple(str(item) for item in changed_files))


def _adopted_candidate_from_record(record: dict[str, Any], current_head: str) -> CandidatePreparationResult | None:
    for key in ("reviewChangesRecoveryAdoption", "recoveryCandidateAdoption"):
        adoption = record.get(key)
        if not isinstance(adoption, dict) or str(adoption.get("status", "")) != "ADOPTED":
            continue
        if str(adoption.get("adoptedCandidateSha", "")) != current_head:
            continue
        changed_files = adoption.get("adoptedChangedFiles", [])
        if not isinstance(changed_files, list):
            continue
        return CandidatePreparationResult("COMMITTED", current_head, tuple(str(item) for item in changed_files))
    return None


def _review_from_mapping(raw: dict[str, Any]) -> ReviewResult:
    violations = tuple(
        ReviewViolation(str(item.get("code", "")), str(item.get("message", "")), {str(key): str(value) for key, value in item.get("evidence", {}).items()})
        for item in raw.get("violations", [])
        if isinstance(item, dict)
    )
    return ReviewResult(
        status=str(raw.get("status", "")),
        decision=str(raw.get("decision", "")),
        reviewed_sha=str(raw.get("reviewed_sha", "")),
        exit_code=int(raw.get("exit_code", 0)),
        stdout=str(raw.get("stdout", "")),
        stderr=str(raw.get("stderr", "")),
        violations=violations,
    )


def transient_review_blocked_evidence(candidate: Any, validation: Any, review: Any) -> tuple[PipelineViolation, ...]:
    candidate_result = _candidate_from_mapping(candidate)
    if candidate_result is None or candidate_result.status != "COMMITTED":
        return (_violation("REVIEW_RESUME_CANDIDATE_INVALID", "review resume requires committed candidate evidence", {}),)
    if not isinstance(validation, dict):
        return (_violation("REVIEW_RESUME_VALIDATION_MISSING", "review resume requires validation evidence", {}),)
    validation_result = _validation_from_mapping(validation)
    if validation_result.status != "PASS":
        return (_violation("REVIEW_RESUME_VALIDATION_NOT_PASSED", "review resume requires previously passed validation", {"status": validation_result.status}),)
    if validation_result.head_before != candidate_result.candidate_sha or validation_result.head_after != candidate_result.candidate_sha:
        return (
            _violation(
                "REVIEW_RESUME_VALIDATION_SHA_MISMATCH",
                "review resume validation evidence does not match candidate",
                {
                    "candidate_sha": candidate_result.candidate_sha,
                    "head_before": validation_result.head_before,
                    "head_after": validation_result.head_after,
                },
            ),
        )
    if not isinstance(review, dict):
        return (_violation("REVIEW_RESUME_REVIEW_EVIDENCE_MISSING", "review resume requires review-block evidence", {}),)
    review_result = _review_from_mapping(review)
    if review_result.status != "BLOCK" or review_result.decision != "Unavailable":
        return (
            _violation(
                "REVIEW_RESUME_REVIEW_STATE_INVALID",
                "review resume requires a blocked unavailable reviewer result",
                {"status": review_result.status, "decision": review_result.decision},
            ),
        )
    if review_result.reviewed_sha != candidate_result.candidate_sha:
        return (
            _violation(
                "REVIEW_RESUME_REVIEW_SHA_MISMATCH",
                "review resume evidence does not match candidate",
                {"candidate_sha": candidate_result.candidate_sha, "reviewed_sha": review_result.reviewed_sha},
            ),
        )
    if not _is_transient_review_block(review_result):
        return (_violation("REVIEW_RESUME_TRANSIENT_FAILURE_UNPROVEN", "review blocker does not prove transient reviewer failure", {}),)
    return ()


def review_changes_requested_evidence(record: Any, candidate: Any, validation: Any, review: Any, record_path: Path | None = None) -> tuple[PipelineViolation, ...]:
    if not isinstance(record, dict):
        return (_violation("REVIEW_CHANGES_RECOVERY_RECORD_MISSING", "Changes Requested recovery requires durable run evidence", {}),)
    candidate_result = _candidate_from_mapping(candidate)
    if candidate_result is None or candidate_result.status != "COMMITTED":
        return (_violation("REVIEW_CHANGES_RECOVERY_CANDIDATE_INVALID", "Changes Requested recovery requires committed candidate evidence", {}),)
    if not isinstance(validation, dict):
        return (_violation("REVIEW_CHANGES_RECOVERY_VALIDATION_MISSING", "Changes Requested recovery requires validation evidence", {}),)
    validation_result = _validation_from_mapping(validation)
    if validation_result.status != "PASS":
        return (_violation("REVIEW_CHANGES_RECOVERY_VALIDATION_NOT_PASSED", "Changes Requested recovery requires previously passed validation", {"status": validation_result.status}),)
    if validation_result.head_before != candidate_result.candidate_sha or validation_result.head_after != candidate_result.candidate_sha:
        return (
            _violation(
                "REVIEW_CHANGES_RECOVERY_VALIDATION_SHA_MISMATCH",
                "Changes Requested recovery validation evidence does not match candidate",
                {
                    "candidate_sha": candidate_result.candidate_sha,
                    "head_before": validation_result.head_before,
                    "head_after": validation_result.head_after,
                },
            ),
        )
    if not isinstance(review, dict):
        return (_violation("REVIEW_CHANGES_RECOVERY_REVIEW_EVIDENCE_MISSING", "Changes Requested recovery requires review evidence", {}),)
    parser_failed = _parser_failed_changes_requested_evidence(record, candidate_result, validation_result, review, record_path)
    if not parser_failed:
        return ()
    durable = _review_changes_requested_durable_block(record, record_path)
    if durable:
        return durable
    review_result = _review_from_mapping(review)
    if review_result.status != "PASS" or review_result.decision != "Changes Requested":
        return (
            _violation(
                "REVIEW_CHANGES_RECOVERY_REVIEW_STATE_INVALID",
                "Changes Requested recovery requires a successful Changes Requested review",
                {"status": review_result.status, "decision": review_result.decision},
            ),
        )
    if review_result.reviewed_sha != candidate_result.candidate_sha:
        return (
            _violation(
                "REVIEW_CHANGES_RECOVERY_REVIEW_SHA_MISMATCH",
                "Changes Requested recovery review evidence does not match candidate",
                {"candidate_sha": candidate_result.candidate_sha, "reviewed_sha": review_result.reviewed_sha},
            ),
        )
    block_sha = _review_changes_requested_block_sha_evidence(record, candidate_result, validation_result, review_result)
    if block_sha:
        return block_sha
    return ()


def _parser_failed_changes_requested_evidence(
    record: Any,
    candidate: CandidatePreparationResult,
    validation: ValidationResult,
    review: Any,
    record_path: Path | None = None,
) -> tuple[PipelineViolation, ...]:
    if not isinstance(record, dict):
        return (_violation("REVIEW_CHANGES_RECOVERY_RECORD_MISSING", "Changes Requested recovery requires durable run evidence", {}),)
    if not isinstance(review, dict):
        return (_violation("REVIEW_CHANGES_RECOVERY_REVIEW_EVIDENCE_MISSING", "Changes Requested recovery requires review evidence", {}),)
    block = record.get("reviewBlock")
    if not isinstance(block, dict):
        return (_violation("REVIEW_CHANGES_RECOVERY_BLOCK_MISSING", "Changes Requested recovery requires review block evidence", {}),)
    if str(block.get("reasonCode", "")) != "REVIEW_DECISION_UNAVAILABLE":
        return (_violation("REVIEW_CHANGES_RECOVERY_PARSER_FAILURE_REASON_MISMATCH", "parser-failed Changes Requested recovery requires REVIEW_DECISION_UNAVAILABLE", {}),)
    if str(block.get("decision", "")) != "Unavailable" or str(block.get("status", "")) != "BLOCK":
        return (_violation("REVIEW_CHANGES_RECOVERY_PARSER_FAILURE_BLOCK_STATE_INVALID", "parser-failed Changes Requested recovery requires unavailable blocked durable state", {}),)
    if str(block.get("blockCause", "")) not in {"", "review_runtime", "review_decision_unavailable"}:
        return (_violation("REVIEW_CHANGES_RECOVERY_PARSER_FAILURE_CAUSE_UNSAFE", "parser-failed Changes Requested recovery requires parser decision block cause", {"blockCause": str(block.get("blockCause", ""))}),)
    if block.get("reasonCodes", []) not in ([], ["REVIEW_DECISION_UNAVAILABLE"]):
        return (_violation("REVIEW_CHANGES_RECOVERY_PARSER_FAILURE_REASON_CODES_UNSAFE", "parser-failed Changes Requested recovery requires only decision-unavailable reason codes", {}),)
    if str(block.get("exitCode", "0")) != "0" or str(block.get("transient", "False")) != "False" or str(block.get("timedOut", "False")) != "False":
        return (_violation("REVIEW_CHANGES_RECOVERY_PARSER_FAILURE_PROCESS_UNSAFE", "parser-failed Changes Requested recovery requires successful non-transient reviewer process evidence", {}),)
    review_result = _review_from_mapping(review)
    if review_result.status != "BLOCK" or review_result.decision != "Unavailable":
        return (_violation("REVIEW_CHANGES_RECOVERY_PARSER_FAILURE_REVIEW_STATE_INVALID", "parser-failed Changes Requested recovery requires unavailable blocked review evidence", {"status": review_result.status, "decision": review_result.decision}),)
    if review_result.exit_code != 0 or review_result.stderr or review_result.reviewed_sha != candidate.candidate_sha:
        return (_violation("REVIEW_CHANGES_RECOVERY_PARSER_FAILURE_REVIEW_PROCESS_UNSAFE", "parser-failed Changes Requested recovery requires successful matching review evidence", {"reviewed_sha": review_result.reviewed_sha, "candidate_sha": candidate.candidate_sha}),)
    codes = tuple(violation.code for violation in review_result.violations)
    if codes != ("REVIEW_DECISION_UNAVAILABLE",):
        return (_violation("REVIEW_CHANGES_RECOVERY_PARSER_FAILURE_REVIEW_CODES_UNSAFE", "parser-failed Changes Requested recovery requires only REVIEW_DECISION_UNAVAILABLE review violation", {"codes": ",".join(codes)}),)
    if parse_review_decision(review_result.stdout) != "Changes Requested":
        return (_violation("REVIEW_CHANGES_RECOVERY_PARSER_FAILURE_DECISION_UNPROVEN", "current parser does not classify the historical review output as Changes Requested", {}),)
    if record_path is None:
        return (_violation("REVIEW_CHANGES_RECOVERY_PARSER_FAILURE_ARTIFACT_EVIDENCE_MISSING", "parser-failed Changes Requested recovery requires durable artifact path evidence", {}),)
    generated = record_path.with_name("review-generated-artifacts.json")
    artifacts = record_path.with_name("review-artifacts")
    if generated.exists() or artifacts.exists():
        return (_violation("REVIEW_CHANGES_RECOVERY_PARSER_FAILURE_REVIEW_ARTIFACTS_PRESENT", "parser-failed Changes Requested recovery is blocked by review artifact evidence", {}),)
    return _review_changes_requested_block_sha_evidence(record, candidate, validation, review_result)


def _review_changes_requested_durable_block(record: Any, record_path: Path | None = None) -> tuple[PipelineViolation, ...]:
    if not isinstance(record, dict):
        return (_violation("REVIEW_CHANGES_RECOVERY_RECORD_MISSING", "Changes Requested recovery requires durable run evidence", {}),)
    if str(record.get("status", "")) != "REVIEW_BLOCKED":
        return (_violation("REVIEW_CHANGES_RECOVERY_STATUS_INVALID", "Changes Requested recovery requires REVIEW_BLOCKED durable state", {"status": str(record.get("status", ""))}),)
    block = record.get("reviewBlock")
    if not isinstance(block, dict):
        return (_violation("REVIEW_CHANGES_RECOVERY_BLOCK_MISSING", "Changes Requested recovery requires review block evidence", {}),)
    reason = str(block.get("reasonCode", ""))
    cause = str(block.get("blockCause", ""))
    if reason == "REVIEW_CHANGES_REQUESTED" and cause == "review_decision":
        return ()
    if _legacy_review_changes_requested_block(block):
        if record_path is None:
            return (_violation("REVIEW_CHANGES_RECOVERY_LEGACY_ARTIFACT_EVIDENCE_MISSING", "legacy Changes Requested recovery requires durable artifact path evidence", {}),)
        generated = record_path.with_name("review-generated-artifacts.json")
        artifacts = record_path.with_name("review-artifacts")
        if generated.exists():
            return (
                _violation(
                    "REVIEW_CHANGES_RECOVERY_LEGACY_REVIEW_ARTIFACTS_PRESENT",
                    "legacy Changes Requested recovery is blocked by generated review artifact evidence",
                    {"artifact": str(generated)},
                ),
            )
        if artifacts.exists():
            return (
                _violation(
                    "REVIEW_CHANGES_RECOVERY_LEGACY_REVIEW_ARTIFACTS_PRESENT",
                    "legacy Changes Requested recovery is blocked by review artifact directory evidence",
                    {"artifact": str(artifacts)},
                ),
            )
        return ()
    return (
        _violation(
            "REVIEW_CHANGES_RECOVERY_BLOCK_CAUSE_UNSAFE",
            "Changes Requested recovery requires a durable review-decision block cause",
            {"reasonCode": reason, "blockCause": cause},
        ),
    )


def _legacy_review_changes_requested_block(block: dict[str, Any]) -> bool:
    return (
        str(block.get("status", "")) == "PASS"
        and str(block.get("decision", "")) == "Changes Requested"
        and str(block.get("reasonCode", "")) == "REVIEW_BLOCK_UNCLASSIFIED"
        and str(block.get("blockCause", "")) == ""
        and block.get("reasonCodes", []) == []
        and str(block.get("exitCode", "0")) == "0"
        and str(block.get("transient", "False")) == "False"
    )


def _review_changes_requested_block_sha_evidence(
    record: Any,
    candidate: CandidatePreparationResult,
    validation: ValidationResult,
    review: ReviewResult,
) -> tuple[PipelineViolation, ...]:
    if not isinstance(record, dict):
        return ()
    block = record.get("reviewBlock")
    if not isinstance(block, dict):
        return ()
    mismatches: dict[str, str] = {}
    candidate_sha = str(block.get("candidateSha", ""))
    validated_sha = str(block.get("validatedSha", ""))
    reviewed_sha = str(block.get("reviewedSha", ""))
    if candidate_sha and candidate_sha != candidate.candidate_sha:
        mismatches["candidateSha"] = candidate_sha
    if validated_sha and validated_sha != validation.head_after:
        mismatches["validatedSha"] = validated_sha
    if reviewed_sha and reviewed_sha != review.reviewed_sha:
        mismatches["reviewedSha"] = reviewed_sha
    if mismatches:
        evidence = dict(mismatches)
        evidence["candidate_sha"] = candidate.candidate_sha
        evidence["validated_sha"] = validation.head_after
        evidence["reviewed_sha"] = review.reviewed_sha
        return (_violation("REVIEW_CHANGES_RECOVERY_BLOCK_SHA_MISMATCH", "Changes Requested recovery review block SHA evidence does not match candidate artifacts", evidence),)
    return ()


def validation_failed_evidence(candidate: Any, validation: Any) -> tuple[PipelineViolation, ...]:
    candidate_result = _candidate_from_mapping(candidate)
    if candidate_result is None or candidate_result.status != "COMMITTED":
        return (_violation("VALIDATION_RESUME_CANDIDATE_INVALID", "validation recovery requires committed candidate evidence", {}),)
    if not isinstance(validation, dict):
        return (_violation("VALIDATION_RESUME_EVIDENCE_MISSING", "validation recovery requires validation evidence", {}),)
    validation_result = _validation_from_mapping(validation)
    if validation_result.status != "BLOCK":
        return (_violation("VALIDATION_RESUME_STATE_INVALID", "validation recovery requires failed validation evidence", {"status": validation_result.status}),)
    if validation_result.head_before != candidate_result.candidate_sha or validation_result.head_after != candidate_result.candidate_sha:
        return (
            _violation(
                "VALIDATION_RESUME_SHA_MISMATCH",
                "validation recovery evidence does not match candidate",
                {
                    "candidate_sha": candidate_result.candidate_sha,
                    "head_before": validation_result.head_before,
                    "head_after": validation_result.head_after,
                },
            ),
        )
    failed = tuple(command for command in validation_result.commands if command.exit_code != 0)
    if not failed and not validation_result.violations:
        return (_violation("VALIDATION_RESUME_FAILURE_UNPROVEN", "validation recovery requires failed command or violation evidence", {}),)
    return ()


def _is_transient_review_block(review: ReviewResult) -> bool:
    codes = tuple(violation.code for violation in review.violations)
    if not codes:
        return False
    if all(code in TRANSIENT_REVIEW_FAILURE_CODES for code in codes):
        return True
    return False


def _review_block_reason_code(review: ReviewResult, block_violations: tuple[PipelineViolation, ...] = ()) -> str:
    if block_violations:
        return block_violations[0].code
    if review.status == "PASS" and review.decision == "Changes Requested":
        return "REVIEW_CHANGES_REQUESTED"
    if _is_transient_review_block(review):
        codes = tuple(violation.code for violation in review.violations)
        return codes[0] if codes else "REVIEWER_UNAVAILABLE"
    codes = tuple(violation.code for violation in review.violations)
    return codes[0] if codes else "REVIEW_BLOCK_UNCLASSIFIED"


def _commit_message(record: dict[str, Any]) -> str:
    return f"spec {record['specNumber']}: {record['featureDescription']}"


def _pr_info(raw: dict[str, Any]) -> PullRequestInfo:
    return PullRequestInfo(
        number=str(raw.get("number", "")),
        url=str(raw.get("url", "")),
        base_branch=str(raw.get("baseRefName", "")),
        head_branch=str(raw.get("headRefName", "")),
        head_sha=str(raw.get("headRefOid", "")),
        mergeable=str(raw.get("mergeable", "")).upper() == "MERGEABLE",
        draft=bool(raw.get("isDraft", False)),
        base_sha=str(raw.get("baseRefOid", "")),
        merge_state_status=str(raw.get("mergeStateStatus", "")),
    )


def _split_command(command: str) -> list[str]:
    if os.name != "nt":
        return shlex.split(command)
    argc = ctypes.c_int()
    command_line_to_argv = ctypes.windll.shell32.CommandLineToArgvW
    command_line_to_argv.argtypes = [ctypes.c_wchar_p, ctypes.POINTER(ctypes.c_int)]
    command_line_to_argv.restype = ctypes.POINTER(ctypes.c_wchar_p)
    local_free = ctypes.windll.kernel32.LocalFree
    local_free.argtypes = [ctypes.c_void_p]
    local_free.restype = ctypes.c_void_p
    argv = command_line_to_argv(command, ctypes.byref(argc))
    if not argv:
        raise ValueError("command could not be parsed")
    try:
        return [argv[index] for index in range(argc.value)]
    finally:
        local_free(argv)


def _resolve_executable(executable: str) -> str:
    resolved = shutil.which(executable)
    return resolved or executable


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return None


def _write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")


def _bounded(value: str | bytes | None) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        value = value.decode("utf-8", errors="replace")
    return value[:20_000]


def _stage(identifier: str, status: str, evidence: dict[str, str]) -> PipelineStage:
    return PipelineStage(identifier, status, evidence)


def _violation(code: str, message: str, evidence: dict[str, str]) -> PipelineViolation:
    return PipelineViolation(code, message, evidence)


def _from_implementer(violation: Any) -> PipelineViolation:
    return PipelineViolation(violation.code, violation.message, violation.evidence)


def _from_validation(violation: Any) -> PipelineViolation:
    return PipelineViolation(violation.code, violation.message, violation.evidence)


def _from_review(violation: Any) -> PipelineViolation:
    return PipelineViolation(violation.code, violation.message, violation.evidence)


def _from_no_change_verification(violation: Any) -> PipelineViolation:
    return PipelineViolation(violation.code, violation.message, violation.evidence)

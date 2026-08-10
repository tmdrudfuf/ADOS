"""End-to-end ADOS run pipeline orchestration."""

from __future__ import annotations

from dataclasses import asdict, dataclass
import ctypes
import json
import os
from pathlib import Path
import re
import shlex
import subprocess
from typing import Any, Protocol

from .exact_head_gate import ExactHeadGate
from .git_provider import GitRepositoryProvider
from .implementer_runtime import ImplementerRuntime, ImplementerRuntimeOutcome
from .primary_repository_guardian import PrimaryRepositoryGuardian
from .project_config import ProjectConfig
from .publication_engine import PublicationEngine, PublicationEvidence, PublicationGateResult
from .repository_provider import RepositoryProviderError
from .review_engine import ReviewEngine, ReviewRequest, ReviewResult, ReviewViolation
from .validation_engine import ValidationCommandResult, ValidationEngine, ValidationResult, ValidationViolation
from .worktree_lifecycle import WorktreeLifecycleEngine, WorktreeRequest, WorktreeLifecycleResult


UNSAFE_TOKENS = ("&", "|", ";", "<", ">", "`", "$(", "\n", "\r")
PIPELINE_READY_STATUSES = {
    "READY_FOR_VALIDATION",
    "VALIDATION_FAILED",
    "READY_FOR_REVIEW",
    "REVIEW_CHANGES_REQUESTED",
    "REVIEW_APPROVED",
    "READY_FOR_PUBLICATION",
    "PR_CREATED",
    "PR_READY",
    "MERGED",
    "CLEANUP_INCOMPLETE",
}


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
        existing = _run(("gh", "pr", "list", "--head", head, "--json", "number,url,baseRefName,headRefName,headRefOid,isDraft,mergeable"), repo)
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
        viewed = _run(("gh", "pr", "view", head, "--json", "number,url,baseRefName,headRefName,headRefOid,isDraft,mergeable"), repo)
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
        self.exact_head = exact_head or ExactHeadGate()
        self.publication = publication or PublicationEngine()
        self.publisher = publisher or GitHubCliPublicationProvider()
        self.lifecycle = lifecycle or WorktreeLifecycleEngine()

    def run(self, *, config: ProjectConfig, run_record_path: Path, timeout_ms: int) -> PipelineOutcome:
        stages: list[PipelineStage] = []
        record = _read_json(run_record_path)
        if not isinstance(record, dict):
            return PipelineOutcome("BLOCKED", (_stage("record", "BLOCKED", {}),), {}, violations=(_violation("RUN_RECORD_INVALID", "run record is unavailable", {"path": str(run_record_path)}),))

        if record.get("status") in {"MERGED", "CLEANUP_INCOMPLETE"}:
            return self._resume_cleanup(config, run_record_path, record, stages)
        if record.get("status") in {"REVIEW_APPROVED", "READY_FOR_PUBLICATION", "PR_CREATED", "PR_READY"}:
            resumed = self._resume_publication(config, run_record_path, record, stages)
            if resumed is not None:
                return resumed

        bootstrap = self._bootstrap(config, record, run_record_path)
        stages.append(_stage("bootstrap", "PASS" if all(item.exit_code == 0 for item in bootstrap) else "BLOCKED", {"commands": str(len(bootstrap))}))
        if any(item.exit_code != 0 for item in bootstrap):
            return PipelineOutcome("BOOTSTRAP_FAILED", tuple(stages), record, bootstrap=bootstrap, violations=(_violation("BOOTSTRAP_FAILED", "bootstrap command failed", {}),))

        if record.get("status") in {"READY_FOR_IMPLEMENTATION", "IMPLEMENTATION_FAILED", "IMPLEMENTATION_TIMED_OUT"}:
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
        for round_number in range(1, max_rounds + 1):
            candidate_result = self._prepare_candidate(config, record)
            stages.append(_stage("candidate", candidate_result.status, {"candidate_sha": candidate_result.candidate_sha, "round": str(round_number)}))
            if candidate_result.status == "BLOCKED":
                return PipelineOutcome("CANDIDATE_BLOCKED", tuple(stages), record, bootstrap=bootstrap, implementer_result=implementer_result, candidate=candidate_result, violations=candidate_result.violations)
            _write_status(run_record_path, record, "READY_FOR_VALIDATION")

            validation_result = self.validation.run(policy=config.execution_policy, repository_path=Path(record["featureWorktree"]))
            _write_json(run_record_path.with_name("validation-runtime.json"), validation_result.to_dict())
            stages.append(_stage("validation", validation_result.status, {"validated_sha": validation_result.head_after}))
            if validation_result.status != "PASS" or validation_result.head_before != validation_result.head_after:
                _write_status(run_record_path, record, "VALIDATION_FAILED")
                return PipelineOutcome("VALIDATION_FAILED", tuple(stages), _read_json(run_record_path), bootstrap=bootstrap, implementer_result=implementer_result, candidate=candidate_result, validation=validation_result, violations=tuple(_from_validation(item) for item in validation_result.violations))

            diff = _git_output(Path(record["featureWorktree"]), "diff", "--no-ext-diff", "--no-color", f"{record['authoritativeBaseSha']}..{candidate_result.candidate_sha}")
            review_scope = f"specs/{record['specNumber']}-{record['featureSlug']}"
            if not (Path(record["featureWorktree"]) / review_scope).exists():
                review_scope = str(record["featureDescription"])
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
            stages.append(_stage("review", review_result.decision, {"reviewed_sha": review_result.reviewed_sha, "round": str(round_number)}))
            if review_result.status != "PASS":
                _write_status(run_record_path, record, "REVIEW_BLOCKED")
                return PipelineOutcome("REVIEW_BLOCKED", tuple(stages), _read_json(run_record_path), bootstrap=bootstrap, implementer_result=implementer_result, candidate=candidate_result, validation=validation_result, review=review_result, violations=tuple(_from_review(item) for item in review_result.violations))
            if review_result.decision == "Approved":
                _write_status(run_record_path, record, "REVIEW_APPROVED")
                break
            if review_result.decision != "Changes Requested":
                _write_status(run_record_path, record, "REVIEW_BLOCKED")
                return PipelineOutcome("REVIEW_BLOCKED", tuple(stages), _read_json(run_record_path), bootstrap=bootstrap, implementer_result=implementer_result, candidate=candidate_result, validation=validation_result, review=review_result, violations=(_violation("REVIEW_DECISION_UNAVAILABLE", "review decision was not Approved or Changes Requested", {}),))
            if round_number == max_rounds:
                _write_status(run_record_path, record, "REVIEW_CHANGES_REQUESTED")
                return PipelineOutcome("REVIEW_CHANGES_REQUESTED", tuple(stages), _read_json(run_record_path), bootstrap=bootstrap, implementer_result=implementer_result, candidate=candidate_result, validation=validation_result, review=review_result, violations=(_violation("REVIEW_MAX_ROUNDS_EXCEEDED", "review changes requested after max rounds", {"max_rounds": str(max_rounds)}),))
            _write_status(run_record_path, record, "READY_FOR_IMPLEMENTATION")
            implementer_result = self.implementer.run(config=config, run_record_path=run_record_path, timeout_ms=timeout_ms)
            record = implementer_result.run_record or _read_json(run_record_path)
            stages.append(_stage("implementer_fix", implementer_result.status, {"round": str(round_number)}))
            if implementer_result.status != "READY_FOR_VALIDATION":
                return PipelineOutcome(implementer_result.status, tuple(stages), record, bootstrap=bootstrap, implementer_result=implementer_result, candidate=candidate_result, validation=validation_result, review=review_result, violations=tuple(_from_implementer(item) for item in implementer_result.violations))

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
                    completed = subprocess.run(tuple(parts), cwd=Path(record["featureWorktree"]), shell=False, capture_output=True, text=True, encoding="utf-8", errors="replace")
                    result = BootstrapCommandResult(command, completed.returncode, _bounded(completed.stdout), _bounded(completed.stderr), False)
                except (FileNotFoundError, OSError) as exc:
                    result = BootstrapCommandResult(command, None, "", str(exc), False)
            results.append(result)
            if result.exit_code != 0:
                break
        _write_json(evidence_path, {"status": "PASS" if all(item.exit_code == 0 for item in results) else "BLOCK", "commands": [item.to_dict() for item in results]})
        return tuple(results)

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
        return CandidatePreparationResult("COMMITTED", head, changed)

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
            return PipelineOutcome("PUBLICATION_BLOCKED", tuple(stages), _read_json(run_record_path), bootstrap=bootstrap, implementer_result=implementer_result, candidate=candidate, validation=validation, review=review, exact_head_gate=exact, violations=(push_violation,))
        remote_sha = self.publisher.remote_head(worktree, record["featureBranch"])
        stages.append(_stage("push", "PASS" if remote_sha == candidate.candidate_sha else "BLOCKED", {"remote_head": remote_sha}))
        pr = self.publisher.create_or_get_draft_pr(worktree, base=config.default_branch, head=record["featureBranch"], title=_commit_message(record), body=f"ADOS run {record['runId']}\n\nValidated SHA: {candidate.candidate_sha}\nReview: {review.decision}")
        if isinstance(pr, PipelineViolation):
            return PipelineOutcome("PUBLICATION_BLOCKED", tuple(stages), _read_json(run_record_path), bootstrap=bootstrap, implementer_result=implementer_result, candidate=candidate, validation=validation, review=review, exact_head_gate=exact, violations=(pr,))
        ready_violation = self.publisher.mark_ready(worktree, pr.number)
        if ready_violation:
            return PipelineOutcome("PUBLICATION_BLOCKED", tuple(stages), _read_json(run_record_path), bootstrap=bootstrap, implementer_result=implementer_result, candidate=candidate, validation=validation, review=review, exact_head_gate=exact, pull_request=pr, violations=(ready_violation,))
        stages.append(_stage("pr", "READY", {"number": pr.number, "head_sha": pr.head_sha}))
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
        primary_update_violations = _update_primary_main(Path(record["primaryRepository"]), config.default_branch)
        if primary_update_violations:
            _write_status(run_record_path, record, "CLEANUP_INCOMPLETE")
            return PipelineOutcome("CLEANUP_INCOMPLETE", tuple(stages), _read_json(run_record_path), bootstrap=bootstrap, implementer_result=implementer_result, candidate=candidate, validation=validation, review=review, exact_head_gate=exact, publication_gate=gate, pull_request=pr, merge=merge, violations=primary_update_violations)
        archive = _archive_evidence(Path(record["primaryRepository"]), record, candidate, validation, review, pr, merge)
        primary_record = _archive_run_record(Path(record["primaryRepository"]), {**record, "pullRequest": pr.number, "mergeCommitSha": merge.merge_commit_sha}, "MERGED")
        remote_delete = self.publisher.delete_remote_branch(Path(record["primaryRepository"]), record["featureBranch"])
        if remote_delete:
            _write_status(run_record_path, record, "CLEANUP_INCOMPLETE")
            _write_status(primary_record, {**record, "pullRequest": pr.number, "mergeCommitSha": merge.merge_commit_sha}, "CLEANUP_INCOMPLETE")
            return PipelineOutcome("CLEANUP_INCOMPLETE", tuple(stages), _read_json(primary_record), bootstrap=bootstrap, implementer_result=implementer_result, candidate=candidate, validation=validation, review=review, exact_head_gate=exact, publication_gate=gate, pull_request=pr, merge=merge, violations=(remote_delete,))
        cleanup = self._cleanup(config, record)
        stages.append(_stage("cleanup", cleanup.status, {"archive": str(archive)}))
        if cleanup.status != "PASS":
            _write_status(run_record_path, record, "CLEANUP_INCOMPLETE")
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

    def _resume_cleanup(self, config: ProjectConfig, run_record_path: Path, record: dict[str, Any], stages: list[PipelineStage]) -> PipelineOutcome:
        canonical_record = _canonical_run_record_path(Path(record["primaryRepository"]), record)
        existing_canonical = _read_json(canonical_record)
        if isinstance(existing_canonical, dict):
            record = {**record, **existing_canonical}
        primary_update_violations = _update_primary_main(Path(record["primaryRepository"]), config.default_branch)
        if primary_update_violations:
            _write_status(canonical_record, record, "CLEANUP_INCOMPLETE")
            return PipelineOutcome("CLEANUP_INCOMPLETE", tuple([*stages, _stage("primary_update", "BLOCKED", {})]), _read_json(canonical_record), violations=primary_update_violations)
        remote_delete = self.publisher.delete_remote_branch(Path(record["primaryRepository"]), record["featureBranch"])
        if remote_delete:
            _write_status(canonical_record, record, "CLEANUP_INCOMPLETE")
            return PipelineOutcome("CLEANUP_INCOMPLETE", tuple([*stages, _stage("remote_branch_delete", "BLOCKED", {})]), _read_json(canonical_record), violations=(remote_delete,))
        cleanup: WorktreeLifecycleResult | None = None
        if Path(record["featureWorktree"]).exists():
            cleanup = self._cleanup(config, record)
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

    def _resume_publication(self, config: ProjectConfig, run_record_path: Path, record: dict[str, Any], stages: list[PipelineStage]) -> PipelineOutcome | None:
        candidate = _read_json(run_record_path.with_name("candidate.json"))
        validation = _read_json(run_record_path.with_name("validation-runtime.json"))
        review = _read_json(run_record_path.with_name("review-runtime.json"))
        if not isinstance(candidate, dict) or not isinstance(validation, dict) or not isinstance(review, dict):
            return None
        candidate_result = CandidatePreparationResult("COMMITTED", str(candidate.get("candidate_sha", "")), tuple(str(item) for item in candidate.get("changed_files", [])))
        validation_result = _validation_from_mapping(validation)
        review_result = _review_from_mapping(review)
        if candidate_result.candidate_sha and validation_result.status == "PASS" and review_result.decision == "Approved":
            exact = self.exact_head.verify(repository_path=Path(record["featureWorktree"]), approved_review_sha=review_result.reviewed_sha, validated_sha=validation_result.head_after)
            stages.append(_stage("exact_head", exact.status, {"approved_review_sha": review_result.reviewed_sha, "validated_sha": validation_result.head_after}))
            if exact.status != "MATCH":
                return PipelineOutcome("EXACT_HEAD_BLOCKED", tuple(stages), record, candidate=candidate_result, validation=validation_result, review=review_result, exact_head_gate=exact.to_dict(), violations=tuple(PipelineViolation(item.code, item.message, item.evidence) for item in exact.violations))
            return self._publish(config, run_record_path, record, stages, (), None, candidate_result, validation_result, review_result, exact.to_dict())
        return None


def _run(args: tuple[str, ...], cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(args, cwd=cwd, shell=False, capture_output=True, text=True, encoding="utf-8", errors="replace")


def _git_output(repo: Path, *args: str) -> str:
    completed = _run(("git", *args), repo)
    return completed.stdout if completed.returncode == 0 else ""


def _write_status(path: Path, record: dict[str, Any], status: str) -> None:
    updated = dict(record)
    updated["status"] = status
    updated["nextStage"] = _next_stage(status)
    _write_json(path, updated)


def _next_stage(status: str) -> str:
    return {
        "READY_FOR_IMPLEMENTATION": "implementation_handoff",
        "READY_FOR_VALIDATION": "validation",
        "VALIDATION_FAILED": "implementation_recovery",
        "READY_FOR_REVIEW": "review",
        "REVIEW_APPROVED": "publication",
        "REVIEW_CHANGES_REQUESTED": "implementation_recovery",
        "CLEANUP_INCOMPLETE": "cleanup",
        "COMPLETE": "complete",
    }.get(status, "recovery")


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
    return archive


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
            exit_code=int(item.get("exit_code", 0)),
            stdout=str(item.get("stdout", "")),
            stderr=str(item.get("stderr", "")),
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


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return None


def _write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")


def _bounded(value: str) -> str:
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

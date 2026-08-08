"""Worktree lifecycle engine."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Iterable

from .execution_policy import ExecutionPolicy
from .primary_repository_guardian import GuardianResult, GuardianViolation, PrimaryRepositoryGuardian
from .repository_provider import RepositoryProviderError
from .worktree_provider import GitWorktreeProvider, WorktreeRecord


@dataclass(frozen=True)
class WorktreeRequest:
    primary_repository_path: Path
    worktree_path: Path
    branch: str
    base_ref: str | None = None
    expected_primary_branch: str | None = None
    expected_primary_head: str | None = None
    allowed_primary_local_paths: tuple[str, ...] = ()


@dataclass(frozen=True)
class WorktreeViolation:
    code: str
    message: str
    evidence: dict[str, str]


@dataclass(frozen=True)
class WorktreeLifecycleResult:
    operation: str
    status: str
    violations: tuple[WorktreeViolation, ...]
    evidence: tuple[dict[str, str], ...]

    def to_dict(self) -> dict[str, object]:
        return {
            "operation": self.operation,
            "status": self.status,
            "violations": [asdict(violation) for violation in self.violations],
            "evidence": list(self.evidence),
        }


class WorktreeLifecycleEngine:
    def __init__(
        self,
        *,
        guardian: PrimaryRepositoryGuardian | None = None,
        provider: GitWorktreeProvider | None = None,
    ) -> None:
        self.guardian = guardian or PrimaryRepositoryGuardian()
        self.provider = provider or GitWorktreeProvider()

    def create(self, *, policy: ExecutionPolicy, request: WorktreeRequest) -> WorktreeLifecycleResult:
        validation = self._validate_request("create", request, require_base_ref=True)
        if validation.status == "BLOCK":
            return validation

        primary_result = self.guardian.audit(
            policy=policy,
            repository_path=request.primary_repository_path,
            expected_branch=request.expected_primary_branch,
            expected_head=request.expected_primary_head,
            allowed_local_paths=request.allowed_primary_local_paths,
        )
        if primary_result.status == "BLOCK":
            return _from_guardian("create", primary_result)

        if request.worktree_path.exists():
            return _block(
                "create",
                WorktreeViolation(
                    "WORKTREE_PATH_EXISTS",
                    "requested worktree path already exists",
                    {"worktree_path": str(request.worktree_path)},
                ),
            )

        try:
            self.provider.create_worktree(
                request.primary_repository_path,
                request.worktree_path,
                request.branch,
                request.base_ref or "",
            )
        except RepositoryProviderError as exc:
            return _block("create", WorktreeViolation(exc.code, exc.message, {"worktree_path": str(request.worktree_path)}))

        return self.verify(policy=policy, request=request, operation="create")

    def verify(
        self,
        *,
        policy: ExecutionPolicy,
        request: WorktreeRequest,
        operation: str = "verify",
    ) -> WorktreeLifecycleResult:
        validation = self._validate_request(operation, request, require_base_ref=False)
        if validation.status == "BLOCK":
            return validation

        record_result = self._find_record(operation, request)
        if isinstance(record_result, WorktreeLifecycleResult):
            return record_result
        record = record_result

        violations: list[WorktreeViolation] = []
        if record.branch != request.branch:
            violations.append(
                WorktreeViolation(
                    "WORKTREE_BRANCH_MISMATCH",
                    "worktree branch does not match expected branch",
                    {"expected": request.branch, "actual": record.branch},
                )
            )

        evidence = (
            {"key": "worktree_path", "value": str(record.path)},
            {"key": "branch", "value": record.branch},
            {"key": "head", "value": record.head},
        )
        return WorktreeLifecycleResult(
            operation=operation,
            status="BLOCK" if violations else "PASS",
            violations=tuple(violations),
            evidence=evidence,
        )

    def remove(self, *, policy: ExecutionPolicy, request: WorktreeRequest) -> WorktreeLifecycleResult:
        validation = self._validate_request("remove", request, require_base_ref=False)
        if validation.status == "BLOCK":
            return validation
        if not policy.cleanup.autonomous:
            return _block(
                "remove",
                WorktreeViolation(
                    "CLEANUP_AUTONOMY_DISABLED",
                    "execution policy does not allow autonomous cleanup",
                    {"autonomous": str(policy.cleanup.autonomous)},
                ),
            )

        record_result = self._find_record("remove", request)
        if isinstance(record_result, WorktreeLifecycleResult):
            return record_result

        try:
            self.provider.remove_worktree(request.primary_repository_path, request.worktree_path)
        except RepositoryProviderError as exc:
            return _block("remove", WorktreeViolation(exc.code, exc.message, {"worktree_path": str(request.worktree_path)}))

        return WorktreeLifecycleResult(
            operation="remove",
            status="PASS",
            violations=(),
            evidence=({"key": "worktree_path", "value": str(request.worktree_path)},),
        )

    def _find_record(self, operation: str, request: WorktreeRequest) -> WorktreeRecord | WorktreeLifecycleResult:
        try:
            records = self.provider.list_worktrees(request.primary_repository_path)
        except RepositoryProviderError as exc:
            return _block(operation, WorktreeViolation(exc.code, exc.message, {"primary_repository_path": str(request.primary_repository_path)}))

        target = request.worktree_path.resolve()
        for record in records:
            if record.path == target:
                return record
        return _block(
            operation,
            WorktreeViolation(
                "WORKTREE_NOT_REGISTERED",
                "requested worktree path is not registered",
                {"worktree_path": str(target)},
            ),
        )

    def _validate_request(
        self,
        operation: str,
        request: WorktreeRequest,
        *,
        require_base_ref: bool,
    ) -> WorktreeLifecycleResult:
        violations: list[WorktreeViolation] = []
        primary = request.primary_repository_path.resolve()
        worktree = request.worktree_path.resolve()
        if primary == worktree:
            violations.append(
                WorktreeViolation(
                    "WORKTREE_EQUALS_PRIMARY",
                    "worktree path must differ from primary repository path",
                    {"path": str(worktree)},
                )
            )
        if not request.branch.strip():
            violations.append(WorktreeViolation("WORKTREE_BRANCH_MISSING", "branch is required", {}))
        if require_base_ref and not (request.base_ref or "").strip():
            violations.append(WorktreeViolation("WORKTREE_BASE_REF_MISSING", "base ref is required", {}))
        return WorktreeLifecycleResult(operation, "BLOCK" if violations else "PASS", tuple(violations), ())


def _from_guardian(operation: str, result: GuardianResult) -> WorktreeLifecycleResult:
    return WorktreeLifecycleResult(
        operation=operation,
        status="BLOCK",
        violations=tuple(
            WorktreeViolation(
                code=f"PRIMARY_{violation.code}",
                message=violation.message,
                evidence=violation.evidence,
            )
            for violation in result.violations
        ),
        evidence=result.evidence,
    )


def _block(operation: str, violation: WorktreeViolation) -> WorktreeLifecycleResult:
    return WorktreeLifecycleResult(operation=operation, status="BLOCK", violations=(violation,), evidence=())

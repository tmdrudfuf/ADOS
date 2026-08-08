"""Primary Repository Guardian implementation."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Iterable

from .execution_policy import ExecutionPolicy, PolicyValidationError
from .git_provider import GitRepositoryProvider
from .repository_provider import RepositoryProvider, RepositoryProviderError


@dataclass(frozen=True)
class GuardianViolation:
    code: str
    message: str
    evidence: dict[str, str]


@dataclass(frozen=True)
class GuardianResult:
    guardian: str
    status: str
    violations: tuple[GuardianViolation, ...]
    evidence: tuple[dict[str, str], ...]

    def to_dict(self) -> dict[str, object]:
        return {
            "guardian": self.guardian,
            "status": self.status,
            "violations": [asdict(item) for item in self.violations],
            "evidence": list(self.evidence),
        }


class PrimaryRepositoryGuardian:
    """Detects unsafe primary repository state without mutating it."""

    guardian_id = "primary_repository"

    def __init__(self, provider: RepositoryProvider | None = None) -> None:
        self.provider = provider or GitRepositoryProvider()

    def audit(
        self,
        *,
        policy: ExecutionPolicy,
        repository_path: str | Path,
        expected_repository_path: str | Path | None = None,
        expected_branch: str | None = None,
        expected_head: str | None = None,
        allowed_local_paths: Iterable[str] = (),
    ) -> GuardianResult:
        violations: list[GuardianViolation] = []
        evidence: list[dict[str, str]] = []

        try:
            if not policy.guardian.stop_on_uncertain:
                violations.append(
                    GuardianViolation(
                        "GUARDIAN_UNCERTAIN_ALLOWED",
                        "execution policy must stop on uncertain guardian state",
                        {"stop_on_uncertain": str(policy.guardian.stop_on_uncertain)},
                    )
                )
        except AttributeError as exc:
            raise PolicyValidationError("POLICY_INVALID_GUARDIAN", "invalid guardian policy") from exc

        repo_path = Path(repository_path).resolve()
        try:
            status = self.provider.status(repo_path)
        except RepositoryProviderError as exc:
            return self._block(
                GuardianViolation(exc.code, exc.message, {"repository_path": str(repo_path)}),
                evidence,
            )

        evidence.extend(
            (
                {"key": "repository_path", "value": str(repo_path)},
                {"key": "repository_root", "value": str(status.root)},
                {"key": "branch", "value": status.branch},
                {"key": "head", "value": status.head},
            )
        )

        if expected_repository_path is not None:
            expected_root = Path(expected_repository_path).resolve()
            if status.root != expected_root:
                violations.append(
                    GuardianViolation(
                        "REPOSITORY_MISMATCH",
                        "repository root does not match expected repository path",
                        {"expected": str(expected_root), "actual": str(status.root)},
                    )
                )

        if expected_branch is not None and status.branch != expected_branch:
            violations.append(
                GuardianViolation(
                    "BRANCH_MISMATCH",
                    "current branch does not match expected branch",
                    {"expected": expected_branch, "actual": status.branch},
                )
            )

        if expected_head is not None and status.head != expected_head:
            violations.append(
                GuardianViolation(
                    "HEAD_MISMATCH",
                    "current HEAD does not match expected HEAD",
                    {"expected": expected_head, "actual": status.head},
                )
            )

        if status.staged:
            violations.append(
                GuardianViolation(
                    "STAGED_FILES",
                    "repository has staged files",
                    {"files": "\n".join(status.staged)},
                )
            )

        if status.dirty_tracked:
            violations.append(
                GuardianViolation(
                    "DIRTY_TRACKED_FILES",
                    "repository has dirty tracked files",
                    {"files": "\n".join(status.dirty_tracked)},
                )
            )

        unexpected_untracked = tuple(
            path for path in status.untracked if not _is_allowed(path, tuple(allowed_local_paths))
        )
        if unexpected_untracked:
            violations.append(
                GuardianViolation(
                    "UNEXPECTED_UNTRACKED_FILES",
                    "repository has unexpected untracked files",
                    {"files": "\n".join(unexpected_untracked)},
                )
            )

        return GuardianResult(
            guardian=self.guardian_id,
            status="BLOCK" if violations else "PASS",
            violations=tuple(violations),
            evidence=tuple(evidence),
        )

    def _block(self, violation: GuardianViolation, evidence: list[dict[str, str]]) -> GuardianResult:
        return GuardianResult(
            guardian=self.guardian_id,
            status="BLOCK",
            violations=(violation,),
            evidence=tuple(evidence),
        )


def _is_allowed(path: str, allowed_patterns: tuple[str, ...]) -> bool:
    normalized = path.replace("\\", "/").rstrip("/")
    for pattern in allowed_patterns:
        allowed = pattern.replace("\\", "/").rstrip("/")
        if not allowed:
            continue
        if normalized == allowed or normalized.startswith(f"{allowed}/"):
            return True
    return False

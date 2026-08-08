"""Exact HEAD Gate."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path

from .git_provider import GitRepositoryProvider
from .repository_provider import RepositoryProvider, RepositoryProviderError


@dataclass(frozen=True)
class ExactHeadGateViolation:
    code: str
    message: str
    evidence: dict[str, str]


@dataclass(frozen=True)
class ExactHeadGateResult:
    status: str
    approved_review_sha: str
    validated_sha: str
    current_head_sha: str
    violations: tuple[ExactHeadGateViolation, ...]

    def to_dict(self) -> dict[str, object]:
        return {
            "status": self.status,
            "approved_review_sha": self.approved_review_sha,
            "validated_sha": self.validated_sha,
            "current_head_sha": self.current_head_sha,
            "violations": [asdict(violation) for violation in self.violations],
        }


class ExactHeadGate:
    def __init__(self, provider: RepositoryProvider | None = None) -> None:
        self.provider = provider or GitRepositoryProvider()

    def verify(self, *, repository_path: str | Path, approved_review_sha: str, validated_sha: str) -> ExactHeadGateResult:
        try:
            current_head = self.provider.current_head(Path(repository_path).resolve())
        except RepositoryProviderError as exc:
            return ExactHeadGateResult(
                "BLOCK",
                approved_review_sha,
                validated_sha,
                "",
                (ExactHeadGateViolation(exc.code, exc.message, {"repository_path": str(repository_path)}),),
            )

        violations: list[ExactHeadGateViolation] = []
        if approved_review_sha != current_head:
            violations.append(
                ExactHeadGateViolation(
                    "APPROVED_REVIEW_SHA_MISMATCH",
                    "approved review SHA does not match current HEAD",
                    {"approved_review_sha": approved_review_sha, "current_head_sha": current_head},
                )
            )
        if validated_sha != current_head:
            violations.append(
                ExactHeadGateViolation(
                    "VALIDATED_SHA_MISMATCH",
                    "validated SHA does not match current HEAD",
                    {"validated_sha": validated_sha, "current_head_sha": current_head},
                )
            )

        return ExactHeadGateResult(
            "BLOCK" if violations else "MATCH",
            approved_review_sha,
            validated_sha,
            current_head,
            tuple(violations),
        )

"""Policy-driven validation engine."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
import subprocess

from .execution_policy import ExecutionPolicy
from .git_provider import GitRepositoryProvider
from .repository_provider import RepositoryProvider, RepositoryProviderError


@dataclass(frozen=True)
class ValidationCommandResult:
    command: str
    exit_code: int
    stdout: str
    stderr: str


@dataclass(frozen=True)
class ValidationViolation:
    code: str
    message: str
    evidence: dict[str, str]


@dataclass(frozen=True)
class ValidationResult:
    status: str
    head_before: str
    head_after: str
    commands: tuple[ValidationCommandResult, ...]
    violations: tuple[ValidationViolation, ...]

    def to_dict(self) -> dict[str, object]:
        return {
            "status": self.status,
            "head_before": self.head_before,
            "head_after": self.head_after,
            "commands": [asdict(command) for command in self.commands],
            "violations": [asdict(violation) for violation in self.violations],
        }


class ValidationEngine:
    def __init__(self, provider: RepositoryProvider | None = None) -> None:
        self.provider = provider or GitRepositoryProvider()

    def run(self, *, policy: ExecutionPolicy, repository_path: str | Path) -> ValidationResult:
        repo = Path(repository_path).resolve()
        try:
            head_before = self.provider.current_head(repo)
        except RepositoryProviderError as exc:
            return ValidationResult(
                status="BLOCK",
                head_before="",
                head_after="",
                commands=(),
                violations=(ValidationViolation(exc.code, exc.message, {"repository_path": str(repo)}),),
            )

        command_results: list[ValidationCommandResult] = []
        violations: list[ValidationViolation] = []
        for command in policy.validation.commands:
            completed = subprocess.run(command, cwd=repo, shell=True, capture_output=True, text=True, encoding="utf-8", errors="replace")
            command_result = ValidationCommandResult(
                command=command,
                exit_code=completed.returncode,
                stdout=_bounded(completed.stdout),
                stderr=_bounded(completed.stderr),
            )
            command_results.append(command_result)
            if completed.returncode != 0:
                violations.append(
                    ValidationViolation(
                        "VALIDATION_COMMAND_FAILED",
                        "validation command exited nonzero",
                        {"command": command, "exit_code": str(completed.returncode)},
                    )
                )

        try:
            head_after = self.provider.current_head(repo)
        except RepositoryProviderError as exc:
            head_after = ""
            violations.append(ValidationViolation(exc.code, exc.message, {"repository_path": str(repo)}))

        if head_before and head_after and head_before != head_after:
            violations.append(
                ValidationViolation(
                    "VALIDATION_HEAD_DRIFT",
                    "repository HEAD changed during validation",
                    {"head_before": head_before, "head_after": head_after},
                )
            )

        return ValidationResult(
            status="BLOCK" if violations else "PASS",
            head_before=head_before,
            head_after=head_after,
            commands=tuple(command_results),
            violations=tuple(violations),
        )


def _bounded(value: str | bytes | None) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        value = value.decode("utf-8", errors="replace")
    return value[:20_000]

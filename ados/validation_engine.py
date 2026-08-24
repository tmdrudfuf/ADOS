"""Policy-driven validation engine."""

from __future__ import annotations

from dataclasses import asdict, dataclass
import os
from pathlib import Path
import signal
import subprocess
import sys

from .execution_policy import ExecutionPolicy
from .git_provider import GitRepositoryProvider
from .repository_provider import RepositoryProvider, RepositoryProviderError


@dataclass(frozen=True)
class ValidationCommandResult:
    command: str
    exit_code: int | None
    stdout: str
    stderr: str
    timed_out: bool = False


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
            completed = _run_validation_command(command, repo, policy.validation.timeout_ms)
            command_result = ValidationCommandResult(
                command=command,
                exit_code=completed.exit_code,
                stdout=_bounded(completed.stdout),
                stderr=_bounded(completed.stderr),
                timed_out=completed.timed_out,
            )
            command_results.append(command_result)
            if completed.timed_out:
                violations.append(
                    ValidationViolation(
                        "VALIDATION_COMMAND_TIMED_OUT",
                        "validation command timed out",
                        {"command": command, "timeout_ms": str(policy.validation.timeout_ms)},
                    )
                )
                break
            if completed.exit_code != 0:
                violations.append(
                    ValidationViolation(
                        "VALIDATION_COMMAND_FAILED",
                        "validation command exited nonzero",
                        {"command": command, "exit_code": str(completed.exit_code)},
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


@dataclass(frozen=True)
class _CompletedValidationCommand:
    exit_code: int | None
    stdout: str | bytes | None
    stderr: str | bytes | None
    timed_out: bool


def _run_validation_command(command: str, cwd: Path, timeout_ms: int) -> _CompletedValidationCommand:
    popen_kwargs = {
        "cwd": cwd,
        "shell": True,
        "stdout": subprocess.PIPE,
        "stderr": subprocess.PIPE,
        "text": True,
        "encoding": "utf-8",
        "errors": "replace",
    }
    if sys.platform == "win32":
        popen_kwargs["creationflags"] = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    else:
        popen_kwargs["start_new_session"] = True

    process = subprocess.Popen(command, **popen_kwargs)
    try:
        stdout, stderr = process.communicate(timeout=timeout_ms / 1000)
        return _CompletedValidationCommand(process.returncode, stdout, stderr, False)
    except subprocess.TimeoutExpired as exc:
        _terminate_process_tree(process)
        stdout = exc.stdout
        stderr = exc.stderr
        try:
            completed_stdout, completed_stderr = process.communicate(timeout=5)
            stdout = completed_stdout if completed_stdout is not None else stdout
            stderr = completed_stderr if completed_stderr is not None else stderr
        except subprocess.TimeoutExpired:
            process.kill()
        return _CompletedValidationCommand(process.returncode, stdout, stderr, True)


def _terminate_process_tree(process: subprocess.Popen[str]) -> None:
    if process.poll() is not None:
        return

    if sys.platform == "win32":
        subprocess.run(
            ("taskkill", "/PID", str(process.pid), "/T", "/F"),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            shell=False,
        )
        return

    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        return

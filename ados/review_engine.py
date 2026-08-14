"""Provider-neutral independent review engine."""

from __future__ import annotations

from dataclasses import asdict, dataclass
import ctypes
import os
from pathlib import Path
import re
import shlex
import subprocess

from .execution_policy import ExecutionPolicy

UNSAFE_TOKENS = ("&", "|", ";", "<", ">", "`", "$(", "\n", "\r")


@dataclass(frozen=True)
class ReviewRequest:
    repository_path: Path
    candidate_sha: str
    base_sha: str
    scope: str
    diff: str = ""


@dataclass(frozen=True)
class ReviewViolation:
    code: str
    message: str
    evidence: dict[str, str]


@dataclass(frozen=True)
class ReviewResult:
    status: str
    decision: str
    reviewed_sha: str
    exit_code: int
    stdout: str
    stderr: str
    violations: tuple[ReviewViolation, ...]

    def to_dict(self) -> dict[str, object]:
        return {
            "status": self.status,
            "decision": self.decision,
            "reviewed_sha": self.reviewed_sha,
            "exit_code": self.exit_code,
            "stdout": self.stdout,
            "stderr": self.stderr,
            "violations": [asdict(violation) for violation in self.violations],
        }


class ReviewEngine:
    def run(self, *, policy: ExecutionPolicy, request: ReviewRequest) -> ReviewResult:
        repository_path = request.repository_path.resolve()
        violations = _verify_request(repository_path, request)
        if violations:
            return ReviewResult("BLOCK", "Unavailable", request.candidate_sha, 0, "", "", violations)

        command_or_violation = _reviewer_command(policy.review.reviewer)
        if isinstance(command_or_violation, ReviewViolation):
            return ReviewResult("BLOCK", "Unavailable", request.candidate_sha, 0, "", "", (command_or_violation,))

        prompt = _prompt(request)
        try:
            completed = subprocess.run(
                command_or_violation,
                input=prompt,
                cwd=repository_path,
                shell=False,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
            )
        except FileNotFoundError:
            return ReviewResult(
                "BLOCK",
                "Unavailable",
                request.candidate_sha,
                0,
                "",
                "",
                (ReviewViolation("REVIEWER_EXECUTABLE_NOT_FOUND", "reviewer executable was not found", {"executable": command_or_violation[0]}),),
            )
        except OSError as exc:
            return ReviewResult(
                "BLOCK",
                "Unavailable",
                request.candidate_sha,
                0,
                "",
                "",
                (ReviewViolation("REVIEWER_SPAWN_FAILED", str(exc), {"repository_path": str(repository_path)}),),
            )
        decision = parse_review_decision(completed.stdout)
        violations: list[ReviewViolation] = []
        if completed.returncode != 0:
            decision = "Unavailable"
            violations.append(
                ReviewViolation(
                    "REVIEWER_COMMAND_FAILED",
                    "reviewer command exited nonzero",
                    {"exit_code": str(completed.returncode)},
                )
            )
        elif decision == "Unavailable":
            violations.append(
                ReviewViolation(
                    "REVIEW_DECISION_UNAVAILABLE",
                    "reviewer output did not contain a supported decision",
                    {},
                )
            )

        return ReviewResult(
            status="BLOCK" if violations else "PASS",
            decision=decision,
            reviewed_sha=request.candidate_sha,
            exit_code=completed.returncode,
            stdout=completed.stdout,
            stderr=completed.stderr,
            violations=tuple(violations),
        )


def parse_review_decision(output: str) -> str:
    for line in output.splitlines():
        normalized = _strip_decision_markup(line)
        normalized = re.sub(r"^Decision:\s*", "", normalized).strip()
        normalized = _strip_decision_markup(normalized)
        if normalized == "Approved":
            return "Approved"
        if normalized == "Changes Requested":
            return "Changes Requested"
    return "Unavailable"


def _strip_decision_markup(value: str) -> str:
    return re.sub(r"^[#>*_\s-]+|[*_\s:]+$", "", value).strip()


def _prompt(request: ReviewRequest) -> str:
    return (
        f"Review exact candidate HEAD: {request.candidate_sha}\n"
        f"Base SHA: {request.base_sha}\n"
        f"Scope: {request.scope}\n\n"
        "Return exactly one top-level decision: Approved or Changes Requested.\n\n"
        f"Diff:\n{request.diff}\n"
    )


def _verify_request(repository_path: Path, request: ReviewRequest) -> tuple[ReviewViolation, ...]:
    violations: list[ReviewViolation] = []
    if not repository_path.is_dir():
        return (ReviewViolation("REVIEW_REPOSITORY_MISSING", "review repository path does not exist", {"repository_path": str(repository_path)}),)
    if not _git_ok(repository_path, "rev-parse", "--show-toplevel"):
        return (ReviewViolation("REVIEW_REPOSITORY_INVALID", "review repository path is not a Git repository", {"repository_path": str(repository_path)}),)
    if not _git_ok(repository_path, "rev-parse", "--verify", f"{request.candidate_sha}^{{commit}}"):
        violations.append(ReviewViolation("REVIEW_CANDIDATE_MISSING", "candidate SHA does not resolve in review repository", {"candidate_sha": request.candidate_sha, "repository_path": str(repository_path)}))
    if not _git_ok(repository_path, "rev-parse", "--verify", f"{request.base_sha}^{{commit}}"):
        violations.append(ReviewViolation("REVIEW_BASE_MISSING", "base SHA does not resolve in review repository", {"base_sha": request.base_sha, "repository_path": str(repository_path)}))
    scope_path = repository_path / request.scope
    if _is_path_scope(request.scope) and not scope_path.exists():
        violations.append(ReviewViolation("REVIEW_SCOPE_MISSING", "review scope path does not exist in review repository", {"scope": request.scope, "repository_path": str(repository_path)}))
    return tuple(violations)


def _git_ok(repository_path: Path, *args: str) -> bool:
    try:
        completed = subprocess.run(("git", *args), cwd=repository_path, check=False, capture_output=True, text=True, encoding="utf-8", errors="replace")
    except (FileNotFoundError, OSError):
        return False
    return completed.returncode == 0


def _is_path_scope(scope: str) -> bool:
    stripped = scope.strip()
    if not stripped or re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*:", stripped):
        return False
    return (
        "/" in stripped
        or "\\" in stripped
        or stripped.startswith(".")
        or stripped.startswith("specs")
        or stripped.startswith("docs")
    )


def _reviewer_command(command: str) -> tuple[str, ...] | ReviewViolation:
    if not command.strip():
        return ReviewViolation("REVIEWER_COMMAND_MISSING", "reviewer command is missing", {})
    if any(token in command for token in UNSAFE_TOKENS):
        return ReviewViolation("REVIEWER_COMMAND_UNSAFE", "reviewer command contains unsafe shell syntax", {"command": command})
    try:
        parts = _split_command(command)
    except ValueError as exc:
        return ReviewViolation("REVIEWER_COMMAND_INVALID", str(exc), {"command": command})
    if not parts:
        return ReviewViolation("REVIEWER_COMMAND_MISSING", "reviewer command is missing", {})
    return tuple(parts)


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

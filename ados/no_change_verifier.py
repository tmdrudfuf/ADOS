"""Independent adjudication for Implementer NO_CHANGES claims."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
import re
import shlex
import subprocess

from .execution_policy import ExecutionPolicy

UNSAFE_TOKENS = ("&", "|", ";", "<", ">", "`", "$(", "\n", "\r")
SUPPORTED_DECISIONS = {"NO_CHANGES_VERIFIED", "FEATURE_MISSING", "AMBIGUOUS"}


@dataclass(frozen=True)
class NoChangeVerificationRequest:
    repository_path: Path
    spec_number: str
    feature_description: str
    candidate_sha: str
    base_sha: str
    implementer_status: str


@dataclass(frozen=True)
class NoChangeVerificationViolation:
    code: str
    message: str
    evidence: dict[str, str]


@dataclass(frozen=True)
class NoChangeVerificationResult:
    status: str
    decision: str
    verified_sha: str
    exit_code: int
    stdout: str
    stderr: str
    violations: tuple[NoChangeVerificationViolation, ...]

    def to_dict(self) -> dict[str, object]:
        return {
            "status": self.status,
            "decision": self.decision,
            "verified_sha": self.verified_sha,
            "exit_code": self.exit_code,
            "stdout": self.stdout,
            "stderr": self.stderr,
            "violations": [asdict(violation) for violation in self.violations],
        }


class NoChangeVerifier:
    def run(self, *, policy: ExecutionPolicy, request: NoChangeVerificationRequest) -> NoChangeVerificationResult:
        command_or_violation = _reviewer_command(policy.review.reviewer)
        if isinstance(command_or_violation, NoChangeVerificationViolation):
            return NoChangeVerificationResult("BLOCK", "AMBIGUOUS", request.candidate_sha, 0, "", "", (command_or_violation,))
        try:
            completed = subprocess.run(
                command_or_violation,
                input=_prompt(request),
                cwd=request.repository_path.resolve(),
                shell=False,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
            )
        except FileNotFoundError:
            return NoChangeVerificationResult("BLOCK", "AMBIGUOUS", request.candidate_sha, 0, "", "", (NoChangeVerificationViolation("NO_CHANGE_VERIFIER_EXECUTABLE_NOT_FOUND", "verifier executable was not found", {"executable": command_or_violation[0]}),))
        except OSError as exc:
            return NoChangeVerificationResult("BLOCK", "AMBIGUOUS", request.candidate_sha, 0, "", "", (NoChangeVerificationViolation("NO_CHANGE_VERIFIER_SPAWN_FAILED", str(exc), {"repository_path": str(request.repository_path)}),))

        decision = parse_no_change_verification_decision(completed.stdout)
        violations: list[NoChangeVerificationViolation] = []
        if completed.returncode != 0:
            decision = "AMBIGUOUS"
            violations.append(NoChangeVerificationViolation("NO_CHANGE_VERIFIER_COMMAND_FAILED", "verifier command exited nonzero", {"exit_code": str(completed.returncode)}))
        elif decision == "AMBIGUOUS":
            violations.append(NoChangeVerificationViolation("NO_CHANGE_VERIFICATION_DECISION_UNAVAILABLE", "verifier output did not contain a supported no-change decision", {}))
        return NoChangeVerificationResult("BLOCK" if violations else "PASS", decision, request.candidate_sha, completed.returncode, _bounded(completed.stdout), _bounded(completed.stderr), tuple(violations))


def parse_no_change_verification_decision(output: str) -> str:
    decisions: list[str] = []
    lines = output.splitlines()
    for index, line in enumerate(lines):
        if _is_decision_heading(line):
            decision = _normalize_decision(_next_non_empty_line(lines, index + 1), allow_suffix=True)
        else:
            decision = _normalize_decision(line, allow_suffix=False)
        if decision in SUPPORTED_DECISIONS:
            decisions.append(decision)
    unique = set(decisions)
    return decisions[0] if len(unique) == 1 else "AMBIGUOUS"


def _prompt(request: NoChangeVerificationRequest) -> str:
    return (
        "ADOS NO_CHANGES Verification\n\n"
        f"Spec: {request.spec_number}\n"
        f"Feature: {request.feature_description}\n"
        f"Repository/worktree: {request.repository_path}\n"
        f"Authoritative base SHA: {request.base_sha}\n"
        f"Current HEAD/candidate SHA: {request.candidate_sha}\n"
        f"Implementer result: {request.implementer_status}\n\n"
        "Inspect actual production code and runtime reachability. Do not treat similarly named tests, docs, mocks, "
        "provider simulations, types, or foundation-only services as proof that the feature exists.\n\n"
        "Return exactly one explicit decision line:\n"
        "NO_CHANGES_VERIFIED\n"
        "FEATURE_MISSING\n"
        "AMBIGUOUS\n\n"
        "If FEATURE_MISSING, describe the concrete missing seam and files/components inspected.\n"
    )


def _reviewer_command(command: str) -> tuple[str, ...] | NoChangeVerificationViolation:
    if not command or any(token in command for token in UNSAFE_TOKENS):
        return NoChangeVerificationViolation("NO_CHANGE_VERIFIER_COMMAND_UNSAFE", "verifier command is missing or unsafe", {"command": command})
    try:
        parts = shlex.split(command, posix=False)
    except ValueError as exc:
        return NoChangeVerificationViolation("NO_CHANGE_VERIFIER_COMMAND_INVALID", str(exc), {"command": command})
    if not parts:
        return NoChangeVerificationViolation("NO_CHANGE_VERIFIER_COMMAND_EMPTY", "verifier command is empty", {})
    return tuple(part.strip('"') for part in parts)


def _is_decision_heading(line: str) -> bool:
    normalized = re.sub(r"^[#>\s-]+", "", line.strip())
    normalized = _strip_emphasis(normalized)
    normalized = re.sub(r"[:\s]+$", "", normalized).strip()
    return normalized.lower() in {"decision", "verification"}


def _next_non_empty_line(lines: list[str], start: int) -> str:
    for line in lines[start:]:
        if line.strip():
            return line
    return ""


def _normalize_decision(line: str, *, allow_suffix: bool) -> str:
    normalized = re.sub(r"^[#>\s-]+", "", line.strip())
    normalized = _strip_emphasis(normalized)
    normalized = re.sub(r"^(?:Decision|Verification):\s*", "", normalized, flags=re.IGNORECASE).strip()
    normalized = _strip_emphasis(normalized)
    normalized = re.sub(r"\s+", " ", normalized).strip()
    pattern = r"^(NO_CHANGES_VERIFIED|FEATURE_MISSING|AMBIGUOUS)"
    pattern += r"(?:\s*(?:[.!?:;]|--|—|-)\s*.*)?$" if allow_suffix else r"$"
    match = re.match(pattern, normalized, flags=re.IGNORECASE)
    return match.group(1).upper() if match else ""


def _strip_emphasis(value: str) -> str:
    normalized = value.strip()
    normalized = re.sub(r"\*\*([^*]+?)\*\*", r"\1", normalized).strip()
    normalized = re.sub(r"__([^_]+?)__", r"\1", normalized).strip()
    for marker in ("**", "__", "*", "_"):
        if normalized.startswith(marker) and normalized.endswith(marker) and len(normalized) > len(marker) * 2:
            normalized = normalized[len(marker) : -len(marker)].strip()
    return normalized


def _bounded(value: str | bytes | None) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        value = value.decode("utf-8", errors="replace")
    return value[:20_000]

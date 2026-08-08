"""Provider-neutral independent review engine."""

from __future__ import annotations

from dataclasses import asdict, dataclass
import re
import subprocess

from .execution_policy import ExecutionPolicy


@dataclass(frozen=True)
class ReviewRequest:
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
        prompt = _prompt(request)
        completed = subprocess.run(
            policy.review.reviewer,
            input=prompt,
            shell=True,
            capture_output=True,
            text=True,
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
        normalized = re.sub(r"^[#>*_\s-]+|[*_\s:]+$", "", line).strip()
        normalized = re.sub(r"^Decision:\s*", "", normalized).strip()
        if normalized == "Approved":
            return "Approved"
        if normalized == "Changes Requested":
            return "Changes Requested"
    return "Unavailable"


def _prompt(request: ReviewRequest) -> str:
    return (
        f"Review exact candidate HEAD: {request.candidate_sha}\n"
        f"Base SHA: {request.base_sha}\n"
        f"Scope: {request.scope}\n\n"
        "Return exactly one top-level decision: Approved or Changes Requested.\n\n"
        f"Diff:\n{request.diff}\n"
    )

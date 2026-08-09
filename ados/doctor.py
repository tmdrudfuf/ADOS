"""Read-only ADOS doctor application service."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
import shlex
import shutil
import subprocess
from typing import Iterable

from . import __version__
from .execution_policy import MERGE_STRATEGIES
from .primary_repository_guardian import PrimaryRepositoryGuardian
from .project_config import ProjectConfig, ProjectConfigError, load_project_config
from .repository_provider import RepositoryProviderError


DISCOVERY_FILENAMES = (
    ".ados/project-config.json",
    ".ados/project.json",
    "ados-project.json",
)


@dataclass(frozen=True)
class DoctorViolation:
    code: str
    message: str
    evidence: dict[str, str]


@dataclass(frozen=True)
class DoctorCheck:
    id: str
    status: str
    summary: str
    blocking: bool
    evidence: dict[str, str]
    violations: tuple[DoctorViolation, ...] = ()
    result_status: str | None = None

    def to_dict(self) -> dict[str, object]:
        return {
            "id": self.id,
            "status": self.status,
            "summary": self.summary,
            "blocking": self.blocking,
            "evidence": dict(self.evidence),
            "violations": [asdict(violation) for violation in self.violations],
        }


@dataclass(frozen=True)
class DoctorResult:
    status: str
    checks: tuple[DoctorCheck, ...]

    def to_dict(self) -> dict[str, object]:
        return {"status": self.status, "checks": [check.to_dict() for check in self.checks]}

@dataclass(frozen=True)
class DoctorRequest:
    project_path: Path
    config_path: Path | None = None


class DoctorService:
    """Coordinates existing ADOS engines for read-only readiness diagnostics."""

    def __init__(self, guardian: PrimaryRepositoryGuardian | None = None) -> None:
        self.guardian = guardian or PrimaryRepositoryGuardian()

    def run(self, request: DoctorRequest) -> DoctorResult:
        checks: list[DoctorCheck] = [
            _pass("ados_runtime", "ADOS runtime", {"version": __version__}),
        ]

        project_path = request.project_path.resolve()
        if not project_path.exists():
            checks.append(_fail("project_path", "Project path", "PROJECT_PATH_MISSING", "project path does not exist", {"path": str(project_path)}, invalid=True))
            return _result(checks)
        if not project_path.is_dir():
            checks.append(_fail("project_path", "Project path", "PROJECT_PATH_NOT_DIRECTORY", "project path is not a directory", {"path": str(project_path)}, invalid=True))
            return _result(checks)
        checks.append(_pass("project_path", "Project path", {"path": str(project_path)}))

        config_path = request.config_path.resolve() if request.config_path else discover_project_config(project_path)
        if config_path is None:
            checks.append(
                _fail(
                    "project_configuration",
                    "Project configuration",
                    "PROJECT_CONFIG_NOT_FOUND",
                    "project configuration was not found",
                    {"project_path": str(project_path), "searched": "\n".join(DISCOVERY_FILENAMES)},
                    blocking=True,
                    invalid=True,
                )
            )
            return _result(checks)

        try:
            config = load_project_config(config_path)
        except ProjectConfigError as exc:
            checks.append(
                _fail(
                    "project_configuration",
                    "Project configuration",
                    exc.code,
                    exc.message,
                    {"config_path": str(config_path)},
                    blocking=True,
                    invalid=True,
                )
            )
            return _result(checks)
        checks.append(_pass("project_configuration", "Project configuration", {"config_path": str(config_path), "project_id": config.project_id}))

        checks.extend(self._project_checks(project_path, config))
        return _result(checks)

    def _project_checks(self, project_path: Path, config: ProjectConfig) -> list[DoctorCheck]:
        checks: list[DoctorCheck] = []
        policy = config.execution_policy
        configured_repo = Path(config.primary_repository_path).resolve()

        checks.append(_pass("execution_policy", "Execution Policy", {"schema_version": policy.schema_version}))
        checks.append(_pass("allowed_local_paths", "Allowed local paths", {"paths": "\n".join(config.allowed_primary_local_paths)}))
        checks.append(_pass("validation_configuration", "Validation configuration", {"commands": "\n".join(policy.validation.commands)}))
        checks.append(_pass("review_rounds", "Review rounds", {"max_rounds": str(policy.review.max_rounds)}))
        checks.append(_pass("archive_cleanup", "Archive and cleanup policy", {"autonomous_cleanup": str(policy.cleanup.autonomous)}))

        if configured_repo != project_path:
            checks.append(
                _fail(
                    "configured_repository_path",
                    "Configured repository path",
                    "PROJECT_REPOSITORY_PATH_MISMATCH",
                    "project configuration primary_repository_path does not match requested project path",
                    {"configured": str(configured_repo), "requested": str(project_path)},
                )
            )
        else:
            checks.append(_pass("configured_repository_path", "Configured repository path", {"path": str(configured_repo)}))

        try:
            root = self.guardian.provider.repository_root(project_path)
            branch = self.guardian.provider.current_branch(project_path)
        except RepositoryProviderError as exc:
            checks.append(_fail("git_repository", "Git repository", exc.code, exc.message, {"path": str(project_path)}))
            return checks

        checks.append(_pass("git_repository", "Git repository", {"root": str(root)}))
        if branch == config.default_branch:
            checks.append(_pass("default_branch", "Default branch", {"branch": branch}))
        else:
            checks.append(_fail("default_branch", "Default branch", "DEFAULT_BRANCH_MISMATCH", "current branch does not match configured default branch", {"expected": config.default_branch, "actual": branch}))

        guardian_result = self.guardian.audit(
            policy=policy,
            repository_path=project_path,
            expected_repository_path=config.primary_repository_path,
            expected_branch=config.default_branch,
            allowed_local_paths=config.allowed_primary_local_paths,
        )
        if guardian_result.status == "PASS":
            checks.append(_pass("primary_repository_guardian", "Primary Repository Guardian", {"status": guardian_result.status}))
        else:
            checks.append(
                DoctorCheck(
                    id="primary_repository_guardian",
                    status="FAIL",
                    summary="Primary Repository Guardian",
                    blocking=True,
                    evidence={"status": guardian_result.status},
                    violations=tuple(
                        DoctorViolation(item.code, item.message, item.evidence)
                        for item in guardian_result.violations
                    ),
                )
            )

        checks.append(_check_executable("git_executable", "Git executable", "git"))
        checks.append(_check_adapter("implementer_adapter", "Implementer", config.implementer, required=False))
        reviewer_command = config.reviewer or policy.review.reviewer
        checks.append(_check_adapter("reviewer_adapter", "Reviewer", reviewer_command, required=True))

        if policy.publication.merge_strategy in MERGE_STRATEGIES:
            checks.append(_pass("publication_policy", "Publication policy", {"merge_strategy": policy.publication.merge_strategy}))
        else:
            checks.append(_fail("publication_policy", "Publication policy", "PUBLICATION_MERGE_STRATEGY_INVALID", "merge strategy is not supported", {"merge_strategy": policy.publication.merge_strategy}))

        checks.append(_pass("worktree_policy", "Worktree policy", {"default_branch": config.default_branch}))
        return checks


def discover_project_config(project_path: Path) -> Path | None:
    for name in DISCOVERY_FILENAMES:
        candidate = project_path / name
        if candidate.is_file():
            return candidate
    return None


def _result(checks: Iterable[DoctorCheck]) -> DoctorResult:
    materialized = tuple(checks)
    if any(check.status == "FAIL" and check.result_status == "INVALID" for check in materialized):
        status = "INVALID"
    elif any(check.status == "FAIL" and check.blocking for check in materialized):
        status = "BLOCKED"
    else:
        status = "READY"
    return DoctorResult(status, materialized)


def _pass(check_id: str, summary: str, evidence: dict[str, str]) -> DoctorCheck:
    return DoctorCheck(check_id, "PASS", summary, False, evidence, ())


def _warn(check_id: str, summary: str, code: str, message: str, evidence: dict[str, str]) -> DoctorCheck:
    return DoctorCheck(check_id, "WARN", summary, False, evidence, (DoctorViolation(code, message, evidence),))


def _fail(
    check_id: str,
    summary: str,
    code: str,
    message: str,
    evidence: dict[str, str],
    *,
    blocking: bool = True,
    invalid: bool = False,
) -> DoctorCheck:
    enriched = dict(evidence)
    if invalid:
        return DoctorCheck(check_id, "FAIL", summary, blocking, enriched, (DoctorViolation(code, message, evidence),), "INVALID")
    return DoctorCheck(check_id, "FAIL", summary, blocking, enriched, (DoctorViolation(code, message, evidence),))


def _check_adapter(check_id: str, label: str, command: str | None, *, required: bool) -> DoctorCheck:
    if not command:
        if required:
            return _fail(check_id, label, "ADAPTER_COMMAND_MISSING", "adapter command is not configured", {})
        return _warn(check_id, label, "ADAPTER_COMMAND_NOT_CONFIGURED", "adapter command is not configured in this project config", {})
    executable = _extract_executable(command)
    if executable is None:
        return _fail(check_id, label, "ADAPTER_COMMAND_UNSAFE", "adapter command could not be parsed safely", {"command": command})
    return _check_executable(check_id, label, executable, command=command)


def _check_executable(check_id: str, summary: str, executable: str, *, command: str | None = None) -> DoctorCheck:
    resolved = shutil.which(executable)
    evidence = {"executable": executable}
    if command:
        evidence["command"] = command
    if not resolved:
        return _fail(check_id, summary, "EXECUTABLE_NOT_FOUND", "configured executable was not found on PATH", evidence)
    evidence["path"] = resolved
    probe = _safe_version_probe(executable, resolved)
    evidence.update(probe)
    return _pass(check_id, summary, evidence)


def _extract_executable(command: str) -> str | None:
    if any(token in command for token in ("&&", "||", ";", "|", ">", "<")):
        return None
    try:
        parts = shlex.split(command, posix=False)
    except ValueError:
        return None
    if not parts:
        return None
    executable = parts[0].strip().strip('"')
    if not executable or any(token in executable for token in ("&&", "||", ";", "|", ">", "<")):
        return None
    return executable


def _safe_version_probe(executable: str, resolved: str) -> dict[str, str]:
    args = [resolved, "--version"]
    try:
        completed = subprocess.run(args, capture_output=True, text=True, timeout=5, shell=False)
    except (OSError, subprocess.SubprocessError) as exc:
        return {"version_probe": "unavailable", "version_error": str(exc)}
    output = (completed.stdout or completed.stderr).strip().splitlines()
    return {
        "version_probe": "ok" if completed.returncode == 0 else "nonzero",
        "version_exit_code": str(completed.returncode),
        "version": output[0][:200] if output else "",
    }

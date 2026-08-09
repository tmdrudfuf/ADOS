"""Provider-neutral implementer invocation runtime."""

from __future__ import annotations

from dataclasses import asdict, dataclass, replace
import ctypes
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import subprocess
from typing import Any

from .git_provider import GitRepositoryProvider
from .primary_repository_guardian import PrimaryRepositoryGuardian
from .project_config import ProjectConfig
from .repository_provider import RepositoryProviderError


SAFE_TIMEOUT_MS = 300_000
UNSAFE_TOKENS = ("&", "|", ";", "<", ">", "`", "$(", "\n", "\r")


@dataclass(frozen=True)
class ImplementerCommand:
    adapter: str
    executable: str
    argv: tuple[str, ...]
    working_directory: str
    timeout_ms: int

    def to_dict(self) -> dict[str, object]:
        return {
            "adapter": self.adapter,
            "executable": self.executable,
            "argv": list(self.argv),
            "workingDirectory": self.working_directory,
            "timeoutMs": self.timeout_ms,
        }


@dataclass(frozen=True)
class ImplementerViolation:
    code: str
    message: str
    evidence: dict[str, str]

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass(frozen=True)
class ImplementerRuntimeRecord:
    runtime_id: str
    run_id: str
    status: str
    command: ImplementerCommand
    handoff: str

    def to_dict(self) -> dict[str, object]:
        return {
            "runtimeId": self.runtime_id,
            "runId": self.run_id,
            "status": self.status,
            "command": self.command.to_dict(),
            "handoff": self.handoff,
        }


@dataclass(frozen=True)
class ImplementerRuntimeResult:
    runtime_id: str
    run_id: str
    status: str
    exit_code: int | None
    timed_out: bool
    stdout: str
    stderr: str
    head_before: str
    head_after: str
    changed_files: tuple[str, ...]
    violations: tuple[ImplementerViolation, ...]

    def to_dict(self) -> dict[str, object]:
        return {
            "runtimeId": self.runtime_id,
            "runId": self.run_id,
            "status": self.status,
            "exitCode": self.exit_code,
            "timedOut": self.timed_out,
            "stdout": self.stdout,
            "stderr": self.stderr,
            "headBefore": self.head_before,
            "headAfter": self.head_after,
            "changedFiles": list(self.changed_files),
            "violations": [violation.to_dict() for violation in self.violations],
        }


@dataclass(frozen=True)
class ImplementerRuntimeOutcome:
    status: str
    runtime: ImplementerRuntimeRecord | None
    result: ImplementerRuntimeResult | None
    run_record: dict[str, Any] | None
    violations: tuple[ImplementerViolation, ...]

    def to_dict(self) -> dict[str, object]:
        return {
            "status": self.status,
            "runtime": self.runtime.to_dict() if self.runtime else None,
            "result": self.result.to_dict() if self.result else None,
            "runRecord": self.run_record,
            "violations": [violation.to_dict() for violation in self.violations],
        }


class ImplementerRuntime:
    def __init__(
        self,
        *,
        git: GitRepositoryProvider | None = None,
        guardian: PrimaryRepositoryGuardian | None = None,
    ) -> None:
        self.git = git or GitRepositoryProvider()
        self.guardian = guardian or PrimaryRepositoryGuardian()

    def run(self, *, config: ProjectConfig, run_record_path: Path, timeout_ms: int = SAFE_TIMEOUT_MS) -> ImplementerRuntimeOutcome:
        record_path = run_record_path.resolve()
        record = _read_record(record_path)
        if isinstance(record, ImplementerViolation):
            return _blocked(record)

        existing = _existing_evidence(record_path)
        if existing is not None and record.get("status") == "READY_FOR_VALIDATION":
            return ImplementerRuntimeOutcome("READY_FOR_VALIDATION", None, None, record, ())

        context_violations = self._validate_context(config, record)
        if context_violations:
            return _blocked(*context_violations, record=record)

        command_or_violation = _resolve_command(record, timeout_ms)
        if isinstance(command_or_violation, ImplementerViolation):
            return _blocked(command_or_violation, record=record)
        command = command_or_violation
        handoff = _handoff(config, record)
        runtime = ImplementerRuntimeRecord(_runtime_id(record["runId"], record["authoritativeBaseSha"]), record["runId"], "Running", command, handoff)

        before_guardian = self.guardian.audit(
            policy=config.execution_policy,
            repository_path=Path(record["primaryRepository"]),
            expected_repository_path=config.primary_repository_path,
            expected_branch=config.default_branch,
            allowed_local_paths=config.allowed_primary_local_paths,
        )
        if before_guardian.status == "BLOCK":
            return _blocked(
                *(
                    ImplementerViolation(f"PRIMARY_{violation.code}", violation.message, violation.evidence)
                    for violation in before_guardian.violations
                ),
                record=record,
                runtime=replace(runtime, status="Blocked"),
            )

        head_before = self.git.current_head(Path(record["featureWorktree"]))
        completed = self._spawn(command, handoff)
        result = self._result(runtime, record, head_before, completed)
        updated_record = _updated_record(record, result.status)

        post_violations = self._postconditions(config, record)
        if post_violations and result.status == "READY_FOR_VALIDATION":
            result = replace(result, status="BLOCKED", violations=tuple(post_violations))
            updated_record = _updated_record(record, "BLOCKED")

        _write_evidence(record_path, replace(runtime, status=result.status), result, updated_record)
        return ImplementerRuntimeOutcome(result.status, replace(runtime, status=result.status), result, updated_record, result.violations)

    def _validate_context(self, config: ProjectConfig, record: dict[str, Any]) -> tuple[ImplementerViolation, ...]:
        violations: list[ImplementerViolation] = []
        required = ("runId", "projectId", "authoritativeBaseSha", "primaryRepository", "featureWorktree", "featureBranch", "implementer", "status")
        for key in required:
            if not isinstance(record.get(key), str) or not record.get(key):
                violations.append(_violation("RUN_RECORD_INVALID", "run record is missing required field", {"field": key}))
        if violations:
            return tuple(violations)
        if record["status"] != "READY_FOR_IMPLEMENTATION":
            violations.append(_violation("RUN_NOT_READY_FOR_IMPLEMENTATION", "run is not ready for implementer invocation", {"status": record["status"]}))
        if record["projectId"] != config.project_id:
            violations.append(_violation("RUN_PROJECT_MISMATCH", "run project does not match configuration", {"run": record["projectId"], "config": config.project_id}))
        if record["implementer"] != (config.implementer or ""):
            violations.append(_violation("IMPLEMENTER_ROLE_MISMATCH", "run implementer does not match configuration", {"run": record["implementer"], "config": config.implementer or ""}))
        primary = Path(record["primaryRepository"]).resolve()
        worktree = Path(record["featureWorktree"]).resolve()
        if primary != Path(config.primary_repository_path).resolve():
            violations.append(_violation("PRIMARY_REPOSITORY_MISMATCH", "run primary repository does not match configuration", {"run": str(primary), "config": str(Path(config.primary_repository_path).resolve())}))
        if not worktree.is_dir():
            violations.append(_violation("WORKTREE_MISSING", "feature worktree does not exist", {"worktree": str(worktree)}))
            return tuple(violations)
        try:
            branch = self.git.current_branch(worktree)
            if branch != record["featureBranch"]:
                violations.append(_violation("WORKTREE_BRANCH_MISMATCH", "feature worktree branch drifted", {"expected": record["featureBranch"], "actual": branch}))
            if not self.git.is_ancestor(worktree, record["authoritativeBaseSha"], self.git.current_head(worktree)):
                violations.append(_violation("WORKTREE_BASE_STALE", "feature HEAD does not descend from recorded base", {"base": record["authoritativeBaseSha"]}))
        except RepositoryProviderError as exc:
            violations.append(_violation(exc.code, exc.message, {"worktree": str(worktree)}))
        return tuple(violations)

    def _spawn(self, command: ImplementerCommand, handoff: str) -> subprocess.CompletedProcess[str] | subprocess.TimeoutExpired[str] | ImplementerViolation:
        try:
            return subprocess.run(
                (command.executable, *command.argv),
                input=handoff,
                cwd=command.working_directory,
                shell=False,
                capture_output=True,
                text=True,
                timeout=command.timeout_ms / 1000,
            )
        except FileNotFoundError:
            return _violation("IMPLEMENTER_EXECUTABLE_NOT_FOUND", "implementer executable was not found", {"executable": command.executable})
        except subprocess.TimeoutExpired as exc:
            return exc
        except OSError as exc:
            return _violation("IMPLEMENTER_SPAWN_FAILED", str(exc), {"executable": command.executable})

    def _result(
        self,
        runtime: ImplementerRuntimeRecord,
        record: dict[str, Any],
        head_before: str,
        completed: subprocess.CompletedProcess[str] | subprocess.TimeoutExpired[str] | ImplementerViolation,
    ) -> ImplementerRuntimeResult:
        worktree = Path(record["featureWorktree"])
        head_after = _safe_head(self.git, worktree)
        changed = _changed_files(self.git, worktree)
        if isinstance(completed, ImplementerViolation):
            return ImplementerRuntimeResult(runtime.runtime_id, runtime.run_id, "BLOCKED", None, False, "", "", head_before, head_after, changed, (completed,))
        if isinstance(completed, subprocess.TimeoutExpired):
            return ImplementerRuntimeResult(
                runtime.runtime_id,
                runtime.run_id,
                "IMPLEMENTATION_TIMED_OUT",
                None,
                True,
                _bounded(completed.stdout or ""),
                _bounded(completed.stderr or ""),
                head_before,
                head_after,
                changed,
                (_violation("IMPLEMENTER_TIMED_OUT", "implementer command timed out", {"timeout_ms": str(runtime.command.timeout_ms)}),),
            )
        if completed.returncode != 0:
            return ImplementerRuntimeResult(
                runtime.runtime_id,
                runtime.run_id,
                "IMPLEMENTATION_FAILED",
                completed.returncode,
                False,
                _bounded(completed.stdout),
                _bounded(completed.stderr),
                head_before,
                head_after,
                changed,
                (_violation("IMPLEMENTER_COMMAND_FAILED", "implementer exited nonzero", {"exit_code": str(completed.returncode)}),),
            )
        return ImplementerRuntimeResult(
            runtime.runtime_id,
            runtime.run_id,
            "READY_FOR_VALIDATION",
            completed.returncode,
            False,
            _bounded(completed.stdout),
            _bounded(completed.stderr),
            head_before,
            head_after,
            changed,
            (),
        )

    def _postconditions(self, config: ProjectConfig, record: dict[str, Any]) -> tuple[ImplementerViolation, ...]:
        violations = list(self._validate_context(config, {**record, "status": "READY_FOR_IMPLEMENTATION"}))
        after_guardian = self.guardian.audit(
            policy=config.execution_policy,
            repository_path=Path(record["primaryRepository"]),
            expected_repository_path=config.primary_repository_path,
            expected_branch=config.default_branch,
            allowed_local_paths=config.allowed_primary_local_paths,
        )
        for violation in after_guardian.violations:
            violations.append(ImplementerViolation(f"PRIMARY_{violation.code}", violation.message, violation.evidence))
        if violations:
            return tuple(violations)
        return ()


def _resolve_command(record: dict[str, Any], timeout_ms: int) -> ImplementerCommand | ImplementerViolation:
    adapter = str(record.get("implementer", ""))
    if not adapter:
        return _violation("IMPLEMENTER_MISSING", "implementer adapter is missing", {})
    if any(token in adapter for token in UNSAFE_TOKENS):
        return _violation("IMPLEMENTER_COMMAND_UNSAFE", "implementer command contains unsafe shell syntax", {"adapter": adapter})
    try:
        parts = _split_command(adapter)
    except ValueError as exc:
        return _violation("IMPLEMENTER_COMMAND_INVALID", str(exc), {"adapter": adapter})
    if not parts:
        return _violation("IMPLEMENTER_MISSING", "implementer adapter is missing", {})
    if timeout_ms <= 0:
        return _violation("IMPLEMENTER_TIMEOUT_INVALID", "timeout must be positive", {"timeout_ms": str(timeout_ms)})
    return ImplementerCommand(adapter, parts[0], tuple(parts[1:]), str(Path(record["featureWorktree"]).resolve()), timeout_ms)


def _handoff(config: ProjectConfig, record: dict[str, Any]) -> str:
    spec_path = Path(record["featureWorktree"]) / "specs" / f"{record['specNumber']}-{record['featureSlug']}"
    return "\n".join(
        [
            "ADOS Implementer Handoff",
            "",
            f"Project: {record['projectId']}",
            f"Primary repository: {record['primaryRepository']}",
            f"Feature worktree: {record['featureWorktree']}",
            f"Feature branch: {record['featureBranch']}",
            f"Authoritative base SHA: {record['authoritativeBaseSha']}",
            f"Spec: {record['specNumber']}",
            f"Feature: {record['featureDescription']}",
            f"Spec Kit path: {spec_path}",
            f"Execution policy version: {config.execution_policy.schema_version}",
            f"Validation commands: {', '.join(config.execution_policy.validation.commands)}",
            f"Reviewer: {record['reviewer']}",
            "",
            "Mutate only the feature worktree. Do not modify the primary repository.",
            "Do not run validation, start review, publish, merge, deploy, or mutate GitHub from this runtime.",
        ]
    )


def _read_record(path: Path) -> dict[str, Any] | ImplementerViolation:
    try:
        raw = json.loads(path.read_text(encoding="utf-8-sig"))
    except OSError as exc:
        return _violation("RUN_RECORD_READ_FAILED", str(exc), {"path": str(path)})
    except json.JSONDecodeError as exc:
        return _violation("RUN_RECORD_INVALID_JSON", str(exc), {"path": str(path)})
    if not isinstance(raw, dict):
        return _violation("RUN_RECORD_INVALID", "run record must be a JSON object", {"path": str(path)})
    return raw


def _existing_evidence(record_path: Path) -> dict[str, Any] | None:
    evidence_path = record_path.with_name("implementer-runtime.json")
    if not evidence_path.is_file():
        return None
    try:
        raw = json.loads(evidence_path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return None
    return raw if isinstance(raw, dict) else None


def _write_evidence(record_path: Path, runtime: ImplementerRuntimeRecord, result: ImplementerRuntimeResult, record: dict[str, Any]) -> None:
    record_path.write_text(json.dumps(record, indent=2, sort_keys=True), encoding="utf-8")
    payload = {
        "status": result.status,
        "runtime": runtime.to_dict(),
        "result": result.to_dict(),
        "runRecord": record,
        "violations": [item.to_dict() for item in result.violations],
    }
    record_path.with_name("implementer-runtime.json").write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")


def _updated_record(record: dict[str, Any], status: str) -> dict[str, Any]:
    updated = dict(record)
    updated["status"] = status
    updated["nextStage"] = "validation" if status == "READY_FOR_VALIDATION" else "implementation_recovery"
    return updated


def _safe_head(git: GitRepositoryProvider, path: Path) -> str:
    try:
        return git.current_head(path)
    except RepositoryProviderError:
        return ""


def _changed_files(git: GitRepositoryProvider, path: Path) -> tuple[str, ...]:
    try:
        status = git.status(path)
    except RepositoryProviderError:
        return ()
    return tuple(status.staged + status.dirty_tracked + status.untracked)


def _runtime_id(run_id: str, base: str) -> str:
    return hashlib.sha256(f"implementer:{run_id}:{base}".encode("utf-8")).hexdigest()[:24]


def _bounded(value: str | bytes) -> str:
    if isinstance(value, bytes):
        value = value.decode("utf-8", errors="replace")
    return value[:20_000]


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


def _blocked(*violations: ImplementerViolation, record: dict[str, Any] | None = None, runtime: ImplementerRuntimeRecord | None = None) -> ImplementerRuntimeOutcome:
    return ImplementerRuntimeOutcome("BLOCKED", runtime, None, record, tuple(violations))


def _violation(code: str, message: str, evidence: dict[str, str]) -> ImplementerViolation:
    return ImplementerViolation(code, message, evidence)

"""Machine-oriented prepare, exact continuation, and inspection protocol."""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
from typing import Any

from .convergence_evidence import CONVERGENCE_ARTIFACT, evaluate_convergence
from .doctor import discover_project_config
from .external_origin import ExternalOriginViolation, parse_external_origin
from .git_provider import GitRepositoryProvider
from .project_config import ProjectConfigError, load_project_config
from .repository_provider import RepositoryProviderError
from .requirements_source import verify_durable_requirements
from .run_command import RunRequest, RunService
from .run_pipeline import RunPipeline
from .worktree_provider import GitWorktreeProvider


@dataclass(frozen=True)
class ExternalRunViolation:
    code: str
    message: str
    evidence: dict[str, str]

    def to_dict(self) -> dict[str, object]:
        return {"code": self.code, "message": self.message, "evidence": self.evidence}


@dataclass(frozen=True)
class ExternalRunResult:
    status: str
    operation: str
    run: dict[str, Any] | None = None
    violations: tuple[ExternalRunViolation, ...] = ()

    def to_dict(self) -> dict[str, object]:
        return {
            "schemaVersion": 1,
            "operation": self.operation,
            "status": self.status,
            "run": self.run,
            "violations": [item.to_dict() for item in self.violations],
        }


class ExternalRunProtocolService:
    def __init__(self, *, runs: RunService | None = None, pipeline: RunPipeline | None = None, git: GitRepositoryProvider | None = None) -> None:
        self.git = git or GitRepositoryProvider()
        self.pipeline = pipeline or RunPipeline()
        self.runs = runs or RunService(pipeline=self.pipeline)
        self.worktrees = GitWorktreeProvider()

    def prepare(
        self, *, project_path: Path, feature: str, origin: dict[str, Any], requirements_file: Path,
        spec_number: int | None = None, config_path: Path | None = None,
    ) -> ExternalRunResult:
        result = self.runs.run(
            RunRequest(
                project_path=project_path,
                feature_description=feature,
                spec_number=spec_number,
                config_path=config_path,
                requirements_file=requirements_file,
                prepare_only=True,
                external_origin=origin,
            )
        )
        if result.status != "PREPARED" or result.run_record is None or result.plan is None:
            return ExternalRunResult(
                result.status,
                "prepare",
                violations=tuple(ExternalRunViolation(v.code, v.message, v.evidence) for v in result.eligibility.violations),
            )
        record = result.run_record.to_dict()
        try:
            actual_head = self.git.current_head(Path(record["featureWorktree"]))
        except RepositoryProviderError:
            actual_head = ""
        return ExternalRunResult("PREPARED", "prepare", _identity(record, actual_head=actual_head, idempotent=result.resumed))

    def continue_exact(
        self, *, project_path: Path, run_id: str, origin_digest: str,
        config_path: Path | None = None, timeout_ms: int = 300000,
    ) -> ExternalRunResult:
        resolved = self._resolve(project_path, run_id, origin_digest, config_path)
        if isinstance(resolved, ExternalRunResult):
            return resolved
        config, record_path, record = resolved
        outcome = self.pipeline.run(config=config, run_record_path=record_path, timeout_ms=timeout_ms)
        current = outcome.run_record if isinstance(outcome.run_record, dict) else _read_json(record_path) or record
        inspected = self._inspection(record_path, current)
        inspected["pipelineResult"] = outcome.to_dict()
        return ExternalRunResult(outcome.status, "continue", inspected)

    def inspect(
        self, *, project_path: Path, run_id: str, origin_digest: str,
        config_path: Path | None = None,
    ) -> ExternalRunResult:
        resolved = self._resolve(project_path, run_id, origin_digest, config_path)
        if isinstance(resolved, ExternalRunResult):
            return resolved
        _, record_path, record = resolved
        return ExternalRunResult("PASS", "inspect", self._inspection(record_path, record))

    def _resolve(self, project_path: Path, run_id: str, origin_digest: str, config_path: Path | None):
        project = project_path.resolve()
        selected_config = config_path.resolve() if config_path else discover_project_config(project)
        if selected_config is None:
            return _blocked("resolve", "PROJECT_CONFIG_NOT_FOUND", "project configuration was not found")
        try:
            config = load_project_config(selected_config)
        except ProjectConfigError as exc:
            return _blocked("resolve", exc.code, exc.message)
        paths: list[Path] = [project / ".agent-workflow" / "runs"]
        try:
            paths.extend(item.path / ".agent-workflow" / "runs" for item in self.worktrees.list_worktrees(project))
        except RepositoryProviderError as exc:
            return _blocked("resolve", exc.code, exc.message)
        matches: list[tuple[Path, dict[str, Any]]] = []
        seen: set[Path] = set()
        for root in paths:
            for candidate in root.glob("*/ados-run.json") if root.is_dir() else ():
                resolved_path = candidate.resolve()
                if resolved_path in seen:
                    continue
                seen.add(resolved_path)
                raw = _read_json(candidate)
                if isinstance(raw, dict) and raw.get("runId") == run_id:
                    matches.append((candidate, raw))
        if not matches:
            return _blocked("resolve", "EXACT_RUN_NOT_FOUND", "the requested run ID was not found", {"runId": run_id})
        if len(matches) != 1:
            return _blocked("resolve", "EXACT_RUN_AMBIGUOUS", "the requested run ID resolved to multiple durable records", {"runId": run_id})
        record_path, record = matches[0]
        violations = self._validate_binding(project, config.project_id, Path(config.primary_repository_path).resolve(), record_path, record, origin_digest)
        if violations:
            return ExternalRunResult("BLOCKED", "resolve", violations=violations)
        return config, record_path, record

    def _validate_binding(self, project: Path, project_id: str, configured_primary: Path, record_path: Path, record: dict[str, Any], origin_digest: str) -> tuple[ExternalRunViolation, ...]:
        violations: list[ExternalRunViolation] = []
        if record.get("projectId") != project_id:
            violations.append(_v("RUN_PROJECT_MISMATCH", "run project does not match configuration"))
        if configured_primary != project:
            violations.append(_v("CONFIG_PRIMARY_MISMATCH", "configuration primary repository does not match the requested project"))
        if Path(str(record.get("primaryRepository", ""))).resolve() != project:
            violations.append(_v("RUN_PRIMARY_MISMATCH", "run primary repository does not match the requested project"))
        durable_digest = str(record.get("externalOriginDigest", ""))
        if not origin_digest or origin_digest != durable_digest:
            violations.append(_v("EXTERNAL_ORIGIN_DIGEST_MISMATCH", "expected origin digest does not match the durable run", {"expected": origin_digest, "actual": durable_digest}))
        parsed = parse_external_origin(record.get("externalOrigin", {}))
        if isinstance(parsed, ExternalOriginViolation):
            violations.append(_v(parsed.code, parsed.message, parsed.evidence))
        elif parsed.digest != durable_digest:
            violations.append(_v("EXTERNAL_ORIGIN_BINDING_CORRUPT", "durable origin fields do not hash to the recorded digest"))
        artifact = _read_json(record_path.with_name("external-origin.json"))
        if not isinstance(artifact, dict) or artifact.get("originDigest") != durable_digest or artifact.get("origin") != record.get("externalOrigin"):
            violations.append(_v("EXTERNAL_ORIGIN_ARTIFACT_MISMATCH", "external origin artifact does not match the run record"))
        requirements = record.get("requirements") if isinstance(record.get("requirements"), dict) else {}
        if isinstance(parsed, ExternalOriginViolation) or requirements.get("sha256") != parsed.requirements_sha256:
            violations.append(_v("EXTERNAL_ORIGIN_REQUIREMENTS_MISMATCH", "origin and durable requirements identities do not match"))
        for item in verify_durable_requirements(record_path, record):
            violations.append(_v(item.code, item.message, item.evidence))
        worktree = Path(str(record.get("featureWorktree", ""))).resolve()
        try:
            status = self.git.status(worktree)
            if status.root != worktree:
                violations.append(_v("WORKTREE_ROOT_MISMATCH", "worktree root does not match durable identity"))
            if status.branch != record.get("featureBranch"):
                violations.append(_v("WORKTREE_BRANCH_MISMATCH", "worktree branch does not match durable identity"))
            if not self.git.is_ancestor(worktree, str(record.get("authoritativeBaseSha", "")), status.head):
                violations.append(_v("WORKTREE_BASE_MISMATCH", "worktree HEAD does not descend from the durable base"))
        except RepositoryProviderError as exc:
            violations.append(_v(exc.code, exc.message))
        return tuple(violations)

    def _inspection(self, record_path: Path, record: dict[str, Any]) -> dict[str, Any]:
        evidence = evaluate_convergence(run_record_path=record_path, record=record, git=self.git)
        stored = _read_json(record_path.with_name(CONVERGENCE_ARTIFACT))
        artifact_integrity = "NOT_WRITTEN"
        if isinstance(stored, dict):
            current_hashes = evidence.get("artifactHashes", {})
            bound_fields = ("runId", "externalOriginDigest", "candidateSha", "headSha", "exactHead", "technicalPublicationReadiness")
            artifact_integrity = "MATCH" if (
                stored.get("artifactHashes") == current_hashes
                and all(stored.get(field) == evidence.get(field) for field in bound_fields)
            ) else "MISMATCH"
        readiness = evidence["technicalPublicationReadiness"]
        blocking_reasons = list(evidence["blockingReasons"])
        if readiness == "READY" and artifact_integrity != "MATCH":
            readiness = "BLOCKED"
            blocking_reasons.append({"code": "CONVERGENCE_ARTIFACT_NOT_CURRENT", "evidence": {"integrity": artifact_integrity}})
        result = _identity(record, actual_head=evidence["headSha"])
        result.update({
            "assignment": evidence["assignment"],
            "candidateSha": evidence["candidateSha"],
            "validation": evidence["validation"],
            "review": evidence["review"],
            "exactHead": evidence["exactHead"],
            "technicalPublicationReadiness": readiness,
            "remotePublicationState": evidence["remotePublicationState"],
            "remotePublicationAuthority": evidence["remotePublicationAuthority"],
            "blockingReasons": blocking_reasons,
            "artifactHashes": evidence["artifactHashes"],
            "convergenceArtifactIntegrity": artifact_integrity,
            "terminal": str(record.get("status", "")) in {"COMPLETE", "NO_CHANGES", "MERGED"},
        })
        return result


def _identity(record: dict[str, Any], *, actual_head: str, idempotent: bool = False) -> dict[str, Any]:
    durable_block = record.get("block") or record.get("reviewBlock") or record.get("implementationRecoveryBlock") or record.get("validationRecoveryBlock")
    return {
        "runId": record.get("runId"), "projectId": record.get("projectId"),
        "specNumber": record.get("specNumber"), "featureSlug": record.get("featureSlug"),
        "featureDescription": record.get("featureDescription"), "branch": record.get("featureBranch"),
        "worktree": record.get("featureWorktree"), "baseSha": record.get("authoritativeBaseSha"),
        "originDigest": record.get("externalOriginDigest"), "externalOrigin": record.get("externalOrigin"),
        "status": record.get("status"), "nextStage": record.get("nextStage"), "actualHead": actual_head,
        "durableBlock": durable_block,
        "idempotentResolution": idempotent,
    }


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _v(code: str, message: str, evidence: dict[str, str] | None = None) -> ExternalRunViolation:
    return ExternalRunViolation(code, message, evidence or {})


def _blocked(operation: str, code: str, message: str, evidence: dict[str, str] | None = None) -> ExternalRunResult:
    return ExternalRunResult("BLOCKED", operation, violations=(_v(code, message, evidence),))

"""Technical convergence evaluation for externally originated runs."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Mapping

from .external_origin import ExternalOriginViolation, parse_external_origin
from .git_provider import GitRepositoryProvider
from .repository_provider import RepositoryProviderError


CONVERGENCE_ARTIFACT = "technical-convergence.json"


def file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def evaluate_convergence(
    *, run_record_path: Path, record: Mapping[str, Any], git: GitRepositoryProvider | None = None
) -> dict[str, Any]:
    git = git or GitRepositoryProvider()
    reasons: list[dict[str, object]] = []
    origin_raw = record.get("externalOrigin")
    origin = parse_external_origin(origin_raw) if isinstance(origin_raw, Mapping) else ExternalOriginViolation("EXTERNAL_ORIGIN_MISSING", "external origin is missing", {})
    expected_digest = str(record.get("externalOriginDigest", ""))
    if isinstance(origin, ExternalOriginViolation):
        reasons.append(origin.to_dict())
        origin_dict: dict[str, object] = dict(origin_raw) if isinstance(origin_raw, Mapping) else {}
    else:
        origin_dict = origin.to_dict()
        if origin.digest != expected_digest:
            reasons.append(_reason("EXTERNAL_ORIGIN_DIGEST_MISMATCH", expected=expected_digest, actual=origin.digest))
        requirements = record.get("requirements") if isinstance(record.get("requirements"), Mapping) else {}
        if requirements.get("sha256") != origin.requirements_sha256:
            reasons.append(_reason("EXTERNAL_ORIGIN_REQUIREMENTS_MISMATCH"))
    origin_artifact = _read_json(run_record_path.with_name("external-origin.json"))
    if not isinstance(origin_artifact, dict) or origin_artifact.get("originDigest") != expected_digest or origin_artifact.get("origin") != origin_dict:
        reasons.append(_reason("EXTERNAL_ORIGIN_ARTIFACT_MISMATCH"))

    candidate = _read_json(run_record_path.with_name("candidate.json"))
    validation = _read_json(run_record_path.with_name("validation-runtime.json"))
    review = _read_json(run_record_path.with_name("review-runtime.json"))
    candidate_sha = str(candidate.get("candidate_sha", candidate.get("candidateSha", ""))) if isinstance(candidate, dict) else ""
    validation_result = validation.get("result", validation) if isinstance(validation, dict) else {}
    review_result = review.get("result", review) if isinstance(review, dict) else {}
    validated_sha = str(validation_result.get("head_after", validation_result.get("headAfter", "")))
    reviewed_sha = str(review_result.get("reviewed_sha", review_result.get("reviewedSha", "")))
    validation_status = str(validation_result.get("status", ""))
    review_status = str(review_result.get("status", ""))
    review_decision = str(review_result.get("decision", ""))
    assignment = record.get("agentAssignment") if isinstance(record.get("agentAssignment"), dict) else {}
    implementer_id = str(assignment.get("implementerId", record.get("implementer", "")))
    reviewer_id = str(assignment.get("reviewerId", record.get("reviewer", "")))
    candidate_owner = str(record.get("candidateOwner", assignment.get("candidateOwnerId", implementer_id)))
    try:
        status = git.status(Path(str(record.get("featureWorktree", ""))))
        head_sha = status.head
        clean = not (status.staged or status.dirty_tracked or status.untracked)
        if status.branch != record.get("featureBranch"):
            reasons.append(_reason("WORKTREE_BRANCH_MISMATCH", expected=str(record.get("featureBranch", "")), actual=status.branch))
    except RepositoryProviderError as exc:
        head_sha, clean = "", False
        reasons.append(_reason(exc.code, message=exc.message))

    checks = (
        (bool(candidate_sha), "CANDIDATE_MISSING"),
        (validation_status == "PASS", "VALIDATION_NOT_PASS"),
        (review_status == "PASS", "REVIEW_RUNTIME_NOT_PASS"),
        (review_decision == "Approved", "REVIEW_NOT_APPROVED"),
        (bool(implementer_id) and bool(reviewer_id) and reviewer_id != implementer_id, "REVIEWER_NOT_INDEPENDENT_FROM_IMPLEMENTER"),
        (bool(candidate_owner) and reviewer_id != candidate_owner, "REVIEWER_NOT_INDEPENDENT_FROM_CANDIDATE_OWNER"),
        (bool(candidate_sha) and candidate_sha == validated_sha, "CANDIDATE_VALIDATION_SHA_MISMATCH"),
        (bool(candidate_sha) and candidate_sha == reviewed_sha, "CANDIDATE_REVIEW_SHA_MISMATCH"),
        (bool(candidate_sha) and candidate_sha == head_sha, "CANDIDATE_HEAD_SHA_MISMATCH"),
        (clean, "WORKTREE_NOT_CLEAN"),
        (not _active_block(record), "ACTIVE_BLOCK_OR_RECOVERY"),
    )
    for passed, code in checks:
        if not passed:
            reasons.append(_reason(code))
    exact = "MATCH" if candidate_sha and candidate_sha == validated_sha == reviewed_sha == head_sha else "MISMATCH"
    if exact != "MATCH":
        reasons.append(_reason("EXACT_HEAD_NOT_MATCH"))

    hashes = {}
    for name in (
        "ados-run.json", "external-origin.json", "requirements-source.json", "requirements-source.md",
        "implementer-runtime.json", "candidate.json", "validation-runtime.json", "review-runtime.json",
    ):
        path = run_record_path.with_name(name)
        if path.is_file():
            hashes[name] = file_sha256(path)
    ready = not reasons
    return {
        "schemaVersion": 1,
        "runId": str(record.get("runId", "")),
        "externalOrigin": origin_dict,
        "externalOriginDigest": expected_digest,
        "assignment": {"implementerId": implementer_id, "candidateOwnerId": candidate_owner, "reviewerId": reviewer_id},
        "candidateSha": candidate_sha,
        "validation": {"status": validation_status, "validatedSha": validated_sha},
        "review": {"status": review_status, "decision": review_decision, "reviewedSha": reviewed_sha},
        "headSha": head_sha,
        "exactHead": exact,
        "technicalPublicationReadiness": "READY" if ready else "BLOCKED",
        "blockingReasons": reasons,
        "remotePublicationState": "NOT_REQUESTED",
        "remotePublicationAuthority": "HUMAN_REQUIRED",
        "artifactHashes": hashes,
    }


def write_convergence_artifact(run_record_path: Path, evidence: Mapping[str, Any]) -> Path:
    path = run_record_path.with_name(CONVERGENCE_ARTIFACT)
    path.write_text(json.dumps(dict(evidence), indent=2, sort_keys=True), encoding="utf-8")
    return path


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _reason(code: str, **evidence: str) -> dict[str, object]:
    return {"code": code, "evidence": evidence}


def _active_block(record: Mapping[str, Any]) -> bool:
    if record.get("block") or record.get("implementationRecoveryBlock") or record.get("reviewConvergenceBlock"):
        return True
    return str(record.get("status", "")) in {"BLOCKED", "REVIEW_BLOCKED", "VALIDATION_FAILED", "IMPLEMENTATION_FAILED", "IMPLEMENTATION_TIMED_OUT"}

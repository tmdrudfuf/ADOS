"""Durable source requirements for ADOS feature runs."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
from pathlib import Path
from typing import Any


REQUIREMENTS_METADATA_FILE = "requirements-source.json"
REQUIREMENTS_CONTENT_FILE = "requirements-source.md"


@dataclass(frozen=True)
class RequirementsSource:
    source_path: str
    content: str
    sha256: str
    content_length: int

    def to_record(self) -> dict[str, Any]:
        return {
            "supplied": True,
            "sourcePath": self.source_path,
            "sha256": self.sha256,
            "contentLength": self.content_length,
            "contentArtifact": REQUIREMENTS_CONTENT_FILE,
            "metadataArtifact": REQUIREMENTS_METADATA_FILE,
        }


@dataclass(frozen=True)
class RequirementsViolation:
    code: str
    message: str
    evidence: dict[str, str]


def read_requirements_file(path: Path) -> RequirementsSource | RequirementsViolation:
    resolved = path.resolve()
    try:
        raw = resolved.read_bytes()
    except OSError as exc:
        return RequirementsViolation("REQUIREMENTS_FILE_READ_FAILED", str(exc), {"path": str(resolved)})
    try:
        content = raw.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        return RequirementsViolation("REQUIREMENTS_FILE_INVALID_UTF8", str(exc), {"path": str(resolved)})
    if not content.strip():
        return RequirementsViolation("REQUIREMENTS_FILE_EMPTY", "requirements file must not be empty", {"path": str(resolved)})
    return RequirementsSource(
        source_path=str(resolved),
        content=content,
        sha256=hash_requirements_content(content),
        content_length=len(content),
    )


def hash_requirements_content(content: str) -> str:
    return hashlib.sha256(_canonical_content(content).encode("utf-8")).hexdigest()


def write_requirements_artifacts(run_record_path: Path, record: dict[str, Any], source: RequirementsSource | None = None) -> None:
    requirements = source.to_record() if source is not None else _requirements_record(record)
    if not requirements:
        return
    content = source.content if source is not None else str(record.get("_requirementsContent", ""))
    if not content:
        return
    content = _canonical_content(content)
    metadata = {key: value for key, value in requirements.items() if not key.startswith("_")}
    run_record_path.with_name(REQUIREMENTS_CONTENT_FILE).write_text(content, encoding="utf-8")
    run_record_path.with_name(REQUIREMENTS_METADATA_FILE).write_text(
        _json_dumps({**metadata, "canonicalSha256": hash_requirements_content(content), "canonicalContentLength": len(content)}),
        encoding="utf-8",
    )


def requirements_prompt_block(run_record_path: Path, record: dict[str, Any]) -> str:
    violations = verify_durable_requirements(run_record_path, record)
    if violations:
        return ""
    content = read_durable_requirements_content(run_record_path, record)
    if not content:
        return ""
    requirements = _requirements_record(record) or {}
    return "\n".join(
        [
            "Authoritative detailed requirements:",
            f"Source path: {requirements.get('sourcePath', '')}",
            f"SHA-256: {requirements.get('sha256', '')}",
            "The requirements below are authoritative for scope and acceptance. Do not narrow, invert, or replace them with the short feature title.",
            "BEGIN AUTHORITATIVE REQUIREMENTS",
            content,
            "END AUTHORITATIVE REQUIREMENTS",
        ]
    )


def read_durable_requirements_content(run_record_path: Path, record: dict[str, Any]) -> str:
    requirements = _requirements_record(record)
    if not requirements:
        return ""
    content_artifact = str(requirements.get("contentArtifact") or REQUIREMENTS_CONTENT_FILE)
    try:
        return run_record_path.with_name(content_artifact).read_text(encoding="utf-8-sig")
    except OSError:
        return ""


def verify_durable_requirements(run_record_path: Path, record: dict[str, Any]) -> tuple[RequirementsViolation, ...]:
    requirements = _requirements_record(record)
    if not requirements:
        return ()
    expected_hash = str(requirements.get("sha256", ""))
    if not expected_hash:
        return (RequirementsViolation("REQUIREMENTS_HASH_MISSING", "durable requirements are missing sha256", {"run_record": str(run_record_path)}),)
    content_artifact = str(requirements.get("contentArtifact") or REQUIREMENTS_CONTENT_FILE)
    content_path = run_record_path.with_name(content_artifact)
    try:
        content = content_path.read_text(encoding="utf-8-sig")
    except OSError as exc:
        return (RequirementsViolation("REQUIREMENTS_ARTIFACT_READ_FAILED", str(exc), {"path": str(content_path)}),)
    actual_hash = hash_requirements_content(content)
    if actual_hash != expected_hash:
        return (
            RequirementsViolation(
                "REQUIREMENTS_HASH_MISMATCH",
                "durable requirements artifact hash does not match the run record",
                {"expected": expected_hash, "actual": actual_hash, "path": str(content_path)},
            ),
        )
    metadata_artifact = str(requirements.get("metadataArtifact") or REQUIREMENTS_METADATA_FILE)
    metadata_path = run_record_path.with_name(metadata_artifact)
    if metadata_path.exists():
        try:
            import json

            metadata = json.loads(metadata_path.read_text(encoding="utf-8-sig"))
        except (OSError, ValueError) as exc:
            return (RequirementsViolation("REQUIREMENTS_METADATA_INVALID", str(exc), {"path": str(metadata_path)}),)
        if isinstance(metadata, dict) and str(metadata.get("canonicalSha256", expected_hash)) != expected_hash:
            return (
                RequirementsViolation(
                    "REQUIREMENTS_METADATA_HASH_MISMATCH",
                    "durable requirements metadata hash does not match the run record",
                    {"expected": expected_hash, "actual": str(metadata.get("canonicalSha256", "")), "path": str(metadata_path)},
                ),
            )
    return ()


def requested_requirements_compatible(record: dict[str, Any], requested: RequirementsSource | None) -> RequirementsViolation | None:
    if requested is None:
        return None
    requirements = _requirements_record(record)
    if not requirements:
        return RequirementsViolation(
            "REQUIREMENTS_RUN_MISSING",
            "an existing run without durable requirements cannot be resumed with a requirements file",
            {"source_path": requested.source_path},
        )
    expected_hash = str(requirements.get("sha256", ""))
    if expected_hash != requested.sha256:
        return RequirementsViolation(
            "REQUIREMENTS_FILE_CHANGED",
            "requirements file content differs from the durable requirements recorded for this run",
            {"expected": expected_hash, "actual": requested.sha256, "source_path": requested.source_path},
        )
    return None


def _requirements_record(record: dict[str, Any]) -> dict[str, Any] | None:
    requirements = record.get("requirements")
    return requirements if isinstance(requirements, dict) and requirements.get("supplied") else None


def _canonical_content(content: str) -> str:
    return content.replace("\r\n", "\n").replace("\r", "\n")


def _json_dumps(value: dict[str, Any]) -> str:
    import json

    return json.dumps(value, indent=2, sort_keys=True)

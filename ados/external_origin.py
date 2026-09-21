"""Versioned, durable identity for externally initiated ADOS runs."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime
import hashlib
import json
import re
from typing import Any, Mapping


EXTERNAL_ORIGIN_SCHEMA_VERSION = 1
ALLOWED_EXTERNAL_PURPOSES = frozenset({"external-project-development"})
MAX_EXTERNAL_RECURSION_DEPTH = 0
_SHA256 = re.compile(r"^[0-9a-f]{64}$")


@dataclass(frozen=True)
class ExternalOriginViolation:
    code: str
    message: str
    evidence: dict[str, str]

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass(frozen=True)
class ExternalOrigin:
    origin_system: str
    origin_schema_version: int
    project_id: str
    backlog_task_id: str
    development_request_id: str
    preparation_id: str
    execution_id: str
    requirements_sha256: str
    requested_feature: str
    purpose: str
    recursion_depth: int
    idempotency_key: str
    created_at: str

    def to_dict(self) -> dict[str, object]:
        return {
            "originSystem": self.origin_system,
            "originSchemaVersion": self.origin_schema_version,
            "projectId": self.project_id,
            "backlogTaskId": self.backlog_task_id,
            "developmentRequestId": self.development_request_id,
            "preparationId": self.preparation_id,
            "executionId": self.execution_id,
            "requirementsSha256": self.requirements_sha256,
            "requestedFeature": self.requested_feature,
            "purpose": self.purpose,
            "recursionDepth": self.recursion_depth,
            "idempotencyKey": self.idempotency_key,
            "createdAt": self.created_at,
        }

    @property
    def digest(self) -> str:
        canonical = json.dumps(self.to_dict(), sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def parse_external_origin(raw: Mapping[str, Any]) -> ExternalOrigin | ExternalOriginViolation:
    if "runId" in raw or "adosRunId" in raw:
        return _violation("EXTERNAL_ORIGIN_RUN_ID_FORBIDDEN", "the caller must not provide an ADOS run ID")
    required = {
        "originSystem", "originSchemaVersion", "projectId", "backlogTaskId",
        "developmentRequestId", "preparationId", "executionId", "requirementsSha256",
        "requestedFeature", "purpose", "recursionDepth", "idempotencyKey", "createdAt",
    }
    missing = sorted(required - set(raw))
    if missing:
        return _violation("EXTERNAL_ORIGIN_FIELDS_MISSING", "external origin is missing required fields", {"fields": ",".join(missing)})
    unknown = sorted(set(raw) - required)
    if unknown:
        return _violation("EXTERNAL_ORIGIN_FIELDS_UNKNOWN", "external origin contains fields outside this schema version", {"fields": ",".join(unknown)})
    if type(raw["originSchemaVersion"]) is not int or type(raw["recursionDepth"]) is not int:
        return _violation("EXTERNAL_ORIGIN_TYPE_INVALID", "schema version and recursion depth must be integers")
    schema = raw["originSchemaVersion"]
    depth = raw["recursionDepth"]
    if schema != EXTERNAL_ORIGIN_SCHEMA_VERSION:
        return _violation("EXTERNAL_ORIGIN_SCHEMA_UNSUPPORTED", "external origin schema version is unsupported", {"actual": str(schema)})
    values = {key: str(raw[key]).strip() for key in required - {"originSchemaVersion", "recursionDepth"}}
    empty = sorted(key for key, value in values.items() if not value)
    if empty:
        return _violation("EXTERNAL_ORIGIN_VALUE_MISSING", "external origin values must be non-empty", {"fields": ",".join(empty)})
    if values["purpose"] not in ALLOWED_EXTERNAL_PURPOSES:
        return _violation("EXTERNAL_ORIGIN_PURPOSE_INVALID", "external origin purpose is not allowed", {"purpose": values["purpose"]})
    if depth < 0 or depth > MAX_EXTERNAL_RECURSION_DEPTH:
        return _violation("EXTERNAL_ORIGIN_RECURSION_BLOCKED", "external child runs may not recursively create verification children", {"recursionDepth": str(depth), "maximum": str(MAX_EXTERNAL_RECURSION_DEPTH)})
    if not _SHA256.fullmatch(values["requirementsSha256"].lower()):
        return _violation("EXTERNAL_ORIGIN_REQUIREMENTS_SHA_INVALID", "requirementsSha256 must be a lowercase SHA-256 digest")
    try:
        timestamp = datetime.fromisoformat(values["createdAt"].replace("Z", "+00:00"))
    except ValueError:
        return _violation("EXTERNAL_ORIGIN_CREATED_AT_INVALID", "createdAt must be an ISO-8601 timestamp")
    if timestamp.tzinfo is None:
        return _violation("EXTERNAL_ORIGIN_CREATED_AT_INVALID", "createdAt must include a timezone")
    return ExternalOrigin(
        values["originSystem"], schema, values["projectId"], values["backlogTaskId"],
        values["developmentRequestId"], values["preparationId"], values["executionId"],
        values["requirementsSha256"].lower(), values["requestedFeature"], values["purpose"],
        depth, values["idempotencyKey"], values["createdAt"],
    )


def origin_artifact(origin: ExternalOrigin) -> dict[str, object]:
    return {"schemaVersion": 1, "originDigest": origin.digest, "origin": origin.to_dict()}


def _violation(code: str, message: str, evidence: dict[str, str] | None = None) -> ExternalOriginViolation:
    return ExternalOriginViolation(code, message, evidence or {})

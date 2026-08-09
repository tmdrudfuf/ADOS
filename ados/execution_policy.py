"""Immutable execution policy model and validation."""

from __future__ import annotations

from dataclasses import asdict, dataclass
import json
from pathlib import Path
from types import MappingProxyType
from typing import Any, Mapping, Sequence


MERGE_STRATEGIES = frozenset({"merge", "squash", "rebase"})


class PolicyValidationError(ValueError):
    """Raised when an execution policy is missing or invalid."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message

    def to_dict(self) -> dict[str, str]:
        return {"code": self.code, "message": self.message}


@dataclass(frozen=True)
class PublicationPolicy:
    merge_strategy: str


@dataclass(frozen=True)
class ReviewPolicy:
    reviewer: str
    max_rounds: int


@dataclass(frozen=True)
class CleanupPolicy:
    autonomous: bool


@dataclass(frozen=True)
class GuardianPolicy:
    stop_on_uncertain: bool


@dataclass(frozen=True)
class ValidationPolicy:
    commands: tuple[str, ...]


@dataclass(frozen=True)
class ExecutionPolicy:
    schema_version: str
    publication: PublicationPolicy
    review: ReviewPolicy
    cleanup: CleanupPolicy
    guardian: GuardianPolicy
    validation: ValidationPolicy

    @classmethod
    def from_mapping(cls, raw: Mapping[str, Any]) -> "ExecutionPolicy":
        root = _require_mapping(raw, "execution_policy", "POLICY_MISSING_ROOT")
        schema_version = _require_non_empty_string(root, "schema_version", "POLICY_MISSING_SCHEMA_VERSION")

        publication = _require_mapping(root, "publication", "POLICY_MISSING_PUBLICATION")
        merge_strategy = _require_non_empty_string(publication, "merge_strategy", "POLICY_MISSING_MERGE_STRATEGY")
        if merge_strategy not in MERGE_STRATEGIES:
            raise PolicyValidationError(
                "POLICY_INVALID_MERGE_STRATEGY",
                "execution_policy.publication.merge_strategy must be one of merge, squash, or rebase",
            )

        review = _require_mapping(root, "review", "POLICY_MISSING_REVIEW")
        reviewer = _require_non_empty_string(review, "reviewer", "POLICY_MISSING_REVIEWER")
        max_rounds = _require_positive_int(review, "max_rounds", "POLICY_INVALID_MAX_ROUNDS")

        cleanup = _require_mapping(root, "cleanup", "POLICY_MISSING_CLEANUP")
        autonomous = _require_bool(cleanup, "autonomous", "POLICY_INVALID_CLEANUP_AUTONOMOUS")

        guardian = _require_mapping(root, "guardian", "POLICY_MISSING_GUARDIAN")
        stop_on_uncertain = _require_bool(guardian, "stop_on_uncertain", "POLICY_INVALID_STOP_ON_UNCERTAIN")

        validation = _require_mapping(root, "validation", "POLICY_MISSING_VALIDATION")
        commands = _require_string_sequence(validation, "commands", "POLICY_INVALID_VALIDATION_COMMANDS")

        return cls(
            schema_version=schema_version,
            publication=PublicationPolicy(merge_strategy=merge_strategy),
            review=ReviewPolicy(reviewer=reviewer, max_rounds=max_rounds),
            cleanup=CleanupPolicy(autonomous=autonomous),
            guardian=GuardianPolicy(stop_on_uncertain=stop_on_uncertain),
            validation=ValidationPolicy(commands=tuple(commands)),
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), sort_keys=True)

    def as_read_only_mapping(self) -> Mapping[str, Any]:
        return MappingProxyType(self.to_dict())


def load_execution_policy(path: str | Path) -> ExecutionPolicy:
    policy_path = Path(path)
    try:
        raw = json.loads(policy_path.read_text(encoding="utf-8-sig"))
    except json.JSONDecodeError as exc:
        raise PolicyValidationError("POLICY_INVALID_JSON", str(exc)) from exc
    except OSError as exc:
        raise PolicyValidationError("POLICY_READ_FAILED", str(exc)) from exc
    if not isinstance(raw, Mapping):
        raise PolicyValidationError("POLICY_INVALID_ROOT", "policy document must be a JSON object")
    return ExecutionPolicy.from_mapping(raw)


def _require_mapping(raw: Mapping[str, Any], key: str, code: str) -> Mapping[str, Any]:
    value = raw.get(key)
    if not isinstance(value, Mapping):
        raise PolicyValidationError(code, f"{key} must be an object")
    return value


def _require_non_empty_string(raw: Mapping[str, Any], key: str, code: str) -> str:
    value = raw.get(key)
    if not isinstance(value, str) or not value.strip():
        raise PolicyValidationError(code, f"{key} must be a non-empty string")
    return value


def _require_positive_int(raw: Mapping[str, Any], key: str, code: str) -> int:
    value = raw.get(key)
    if not isinstance(value, int) or isinstance(value, bool) or value < 1:
        raise PolicyValidationError(code, f"{key} must be a positive integer")
    return value


def _require_bool(raw: Mapping[str, Any], key: str, code: str) -> bool:
    value = raw.get(key)
    if not isinstance(value, bool):
        raise PolicyValidationError(code, f"{key} must be a boolean")
    return value


def _require_string_sequence(raw: Mapping[str, Any], key: str, code: str) -> Sequence[str]:
    value = raw.get(key)
    if not isinstance(value, list) or not value:
        raise PolicyValidationError(code, f"{key} must be a non-empty list of strings")
    if any(not isinstance(item, str) or not item.strip() for item in value):
        raise PolicyValidationError(code, f"{key} must contain only non-empty strings")
    return value

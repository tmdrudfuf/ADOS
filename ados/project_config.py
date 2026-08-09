"""Project configuration model."""

from __future__ import annotations

from dataclasses import asdict, dataclass
import json
from pathlib import Path
from typing import Any, Mapping

from .execution_policy import ExecutionPolicy, PolicyValidationError


class ProjectConfigError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message

    def to_dict(self) -> dict[str, str]:
        return {"code": self.code, "message": self.message}


@dataclass(frozen=True)
class ProjectConfig:
    project_id: str
    primary_repository_path: str
    default_branch: str
    allowed_primary_local_paths: tuple[str, ...]
    execution_policy: ExecutionPolicy
    implementer: str | None = None
    reviewer: str | None = None

    @classmethod
    def from_mapping(cls, raw: Mapping[str, Any]) -> "ProjectConfig":
        project = _require_mapping(raw, "project", "PROJECT_CONFIG_MISSING_PROJECT")
        project_id = _require_string(project, "id", "PROJECT_CONFIG_MISSING_ID")
        primary_repository_path = _require_string(project, "primary_repository_path", "PROJECT_CONFIG_MISSING_PRIMARY_REPOSITORY_PATH")
        default_branch = _require_string(project, "default_branch", "PROJECT_CONFIG_MISSING_DEFAULT_BRANCH")
        allowed_paths = _require_string_list(project, "allowed_primary_local_paths", "PROJECT_CONFIG_INVALID_ALLOWED_PATHS")
        roles = raw.get("roles", {})
        if roles is None:
            roles = {}
        if not isinstance(roles, Mapping):
            raise ProjectConfigError("PROJECT_CONFIG_INVALID_ROLES", "roles must be an object")
        implementer = _optional_string(roles, "implementer", "PROJECT_CONFIG_INVALID_IMPLEMENTER")
        reviewer = _optional_string(roles, "reviewer", "PROJECT_CONFIG_INVALID_REVIEWER")
        try:
            execution_policy = ExecutionPolicy.from_mapping(raw)
        except PolicyValidationError as exc:
            raise ProjectConfigError(exc.code, exc.message) from exc
        return cls(project_id, primary_repository_path, default_branch, tuple(allowed_paths), execution_policy, implementer, reviewer)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["execution_policy"] = self.execution_policy.to_dict()
        return data


def load_project_config(path: str | Path) -> ProjectConfig:
    config_path = Path(path)
    try:
        raw = json.loads(config_path.read_text(encoding="utf-8-sig"))
    except json.JSONDecodeError as exc:
        raise ProjectConfigError("PROJECT_CONFIG_INVALID_JSON", str(exc)) from exc
    except OSError as exc:
        raise ProjectConfigError("PROJECT_CONFIG_READ_FAILED", str(exc)) from exc
    if not isinstance(raw, Mapping):
        raise ProjectConfigError("PROJECT_CONFIG_INVALID_ROOT", "project config must be a JSON object")
    return ProjectConfig.from_mapping(raw)


def _require_mapping(raw: Mapping[str, Any], key: str, code: str) -> Mapping[str, Any]:
    value = raw.get(key)
    if not isinstance(value, Mapping):
        raise ProjectConfigError(code, f"{key} must be an object")
    return value


def _require_string(raw: Mapping[str, Any], key: str, code: str) -> str:
    value = raw.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ProjectConfigError(code, f"{key} must be a non-empty string")
    return value


def _optional_string(raw: Mapping[str, Any], key: str, code: str) -> str | None:
    value = raw.get(key)
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise ProjectConfigError(code, f"{key} must be a non-empty string when provided")
    return value


def _require_string_list(raw: Mapping[str, Any], key: str, code: str) -> tuple[str, ...]:
    value = raw.get(key)
    if not isinstance(value, list):
        raise ProjectConfigError(code, f"{key} must be a list")
    if any(not isinstance(item, str) or not item.strip() for item in value):
        raise ProjectConfigError(code, f"{key} must contain only non-empty strings")
    return tuple(value)

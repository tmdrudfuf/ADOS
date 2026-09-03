"""Provider-neutral adaptive implementer / reviewer role selection.

This module adds fixed and adaptive agent-role assignment on top of the existing
ADOS execution policy. It never spawns configured agent commands, never invents
token/usage numbers, and fails closed when no independent reviewer can be chosen.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
import shutil
from typing import Any, Iterable, Mapping

from .execution_policy import PolicyValidationError
from .review_engine import UNSAFE_TOKENS, _split_command


# --------------------------------------------------------------------------- #
# Runtime failure classification (requirement 14)                             #
# --------------------------------------------------------------------------- #

RUNTIME_FAILURE_CATEGORIES: tuple[str, ...] = (
    "AUTHENTICATION_UNAVAILABLE",
    "QUOTA_EXHAUSTED",
    "USAGE_LIMIT_REACHED",
    "CAPACITY_UNAVAILABLE",
    "COMMAND_NOT_FOUND",
    "TRANSIENT_RUNTIME_UNAVAILABLE",
    "UNKNOWN_RUNTIME_FAILURE",
)

# External availability failures are NOT implementation defects: they must not
# consume the implementation-defect recovery budget. UNKNOWN is deliberately
# excluded so unknown failures fail conservatively as ordinary defects.
EXTERNAL_AVAILABILITY_CATEGORIES = frozenset(RUNTIME_FAILURE_CATEGORIES) - {"UNKNOWN_RUNTIME_FAILURE"}

# ponytail: substring heuristic, curated markers only. Upgrade to structured
# provider evidence if/when a CLI exposes a machine-readable health signal.
_CATEGORY_MARKERS: tuple[tuple[str, tuple[str, ...]], ...] = (
    (
        "AUTHENTICATION_UNAVAILABLE",
        (
            "oauth session expired and could not be refreshed",
            "oauth session expired",
            "oauth token",
            "session expired",
            "not authenticated",
            "not logged in",
            "please log in",
            "please sign in",
            "please run `claude login`",
            "please run 'claude login'",
            "run `codex login`",
            "authentication failed",
            "authentication required",
            "unauthorized",
            "invalid api key",
            "missing api key",
            "http 401",
            "status 401",
            "error 401",
        ),
    ),
    (
        "QUOTA_EXHAUSTED",
        (
            "quota exhausted",
            "quota exceeded",
            "insufficient_quota",
            "insufficient quota",
            "out of credits",
            "not enough credits",
            "billing hard limit",
            "payment required",
            "http 402",
            "error 402",
        ),
    ),
    (
        "USAGE_LIMIT_REACHED",
        (
            "usage limit reached",
            "usage limit",
            "you have hit your usage limit",
            "monthly limit",
            "rate limit",
            "rate-limit",
            "rate limited",
            "too many requests",
            "http 429",
            "status 429",
            "error 429",
        ),
    ),
    (
        "CAPACITY_UNAVAILABLE",
        (
            "capacity unavailable",
            "no capacity",
            "at capacity",
            "overloaded",
            "server is overloaded",
            "service unavailable",
            "temporarily unavailable",
            "http 503",
            "status 503",
            "error 503",
        ),
    ),
    (
        "TRANSIENT_RUNTIME_UNAVAILABLE",
        (
            "connection reset",
            "connection refused",
            "connection closed",
            "network error",
            "econnreset",
            "etimedout",
            "socket hang up",
            "temporary failure in name resolution",
            "bad gateway",
            "http 502",
            "http 500",
            "internal server error",
        ),
    ),
)


def classify_runtime_failure(
    *,
    exit_code: int | None = None,
    stdout: str = "",
    stderr: str = "",
    timed_out: bool = False,
    executable_found: bool = True,
) -> str:
    """Deterministically classify a CLI/runtime failure.

    Conservative: anything without stable textual evidence is
    ``UNKNOWN_RUNTIME_FAILURE`` and is treated as an ordinary implementation
    defect by callers. A bare timeout is not assumed to be a quota failure.
    """

    if not executable_found:
        return "COMMAND_NOT_FOUND"
    haystack = f"{stdout}\n{stderr}".lower()
    for category, markers in _CATEGORY_MARKERS:
        if any(marker in haystack for marker in markers):
            return category
    return "UNKNOWN_RUNTIME_FAILURE"


def is_external_availability_failure(category: str) -> bool:
    """True when the category is an external availability event, not a defect."""

    return category in EXTERNAL_AVAILABILITY_CATEGORIES


def availability_state_for_category(category: str) -> str:
    """Map a runtime failure category to a durable, non-fabricated agent state."""

    return {
        "QUOTA_EXHAUSTED": "unavailable_quota",
        "USAGE_LIMIT_REACHED": "unavailable_quota",
        "CAPACITY_UNAVAILABLE": "unavailable_capacity",
        "AUTHENTICATION_UNAVAILABLE": "unavailable_auth",
        "COMMAND_NOT_FOUND": "unavailable",
        "TRANSIENT_RUNTIME_UNAVAILABLE": "unavailable",
    }.get(category, "unknown")


# --------------------------------------------------------------------------- #
# Health probe (requirement 17): read-only, never spawns                      #
# --------------------------------------------------------------------------- #


def probe_agent(command: str) -> str:
    """Non-mutating agent health probe.

    Only checks shell-safety and executable resolvability. It never executes the
    configured command and never inspects provider usage. Returns one of
    ``available``, ``unavailable`` or ``unknown``.
    """

    if not command or not command.strip():
        return "unknown"
    if any(token in command for token in UNSAFE_TOKENS):
        return "unavailable"
    try:
        parts = _split_command(command)
    except ValueError:
        return "unavailable"
    if not parts:
        return "unknown"
    return "available" if shutil.which(parts[0]) else "unavailable"


# --------------------------------------------------------------------------- #
# Role policy model (requirement 2)                                           #
# --------------------------------------------------------------------------- #

_VALID_MODES = frozenset({"fixed", "adaptive"})


@dataclass(frozen=True)
class AgentRolePolicy:
    mode: str
    agents: Mapping[str, str]
    implementer_preference: tuple[str, ...]
    reviewer_preference: tuple[str, ...]

    @classmethod
    def from_mapping(cls, raw: Mapping[str, Any]) -> "AgentRolePolicy":
        if not isinstance(raw, Mapping):
            raise PolicyValidationError("POLICY_INVALID_AGENT_ROLES", "agent_roles must be an object")
        mode = raw.get("mode", "fixed")
        if not isinstance(mode, str) or mode not in _VALID_MODES:
            raise PolicyValidationError(
                "POLICY_INVALID_AGENT_ROLES_MODE",
                "agent_roles.mode must be 'fixed' or 'adaptive'",
            )
        agents_raw = raw.get("agents")
        if not isinstance(agents_raw, Mapping) or not agents_raw:
            raise PolicyValidationError(
                "POLICY_INVALID_AGENT_ROLES_AGENTS",
                "agent_roles.agents must be a non-empty object of agent id to command",
            )
        agents: dict[str, str] = {}
        for agent_id, command in agents_raw.items():
            if not isinstance(agent_id, str) or not agent_id.strip():
                raise PolicyValidationError("POLICY_INVALID_AGENT_ROLES_AGENTS", "agent_roles.agents keys must be non-empty strings")
            if not isinstance(command, str) or not command.strip():
                raise PolicyValidationError(
                    "POLICY_INVALID_AGENT_ROLES_AGENTS",
                    f"agent_roles.agents['{agent_id}'] must be a non-empty command string",
                )
            agents[agent_id] = command
        implementer_preference = cls._preference(raw, "implementer_preference", agents)
        reviewer_preference = cls._preference(raw, "reviewer_preference", agents)
        return cls(
            mode=mode,
            agents=agents,
            implementer_preference=implementer_preference,
            reviewer_preference=reviewer_preference,
        )

    @staticmethod
    def _preference(raw: Mapping[str, Any], key: str, agents: Mapping[str, str]) -> tuple[str, ...]:
        value = raw.get(key)
        if not isinstance(value, list) or not value:
            raise PolicyValidationError(
                f"POLICY_INVALID_AGENT_ROLES_{key.upper()}",
                f"agent_roles.{key} must be a non-empty list of agent ids",
            )
        seen: list[str] = []
        for item in value:
            if not isinstance(item, str) or item not in agents:
                raise PolicyValidationError(
                    f"POLICY_INVALID_AGENT_ROLES_{key.upper()}",
                    f"agent_roles.{key} entries must reference a configured agent id",
                )
            if item not in seen:
                seen.append(item)
        return tuple(seen)

    def command_for(self, agent_id: str) -> str:
        return self.agents.get(agent_id, "")

    def to_dict(self) -> dict[str, Any]:
        return {
            "mode": self.mode,
            "agents": dict(self.agents),
            "implementer_preference": list(self.implementer_preference),
            "reviewer_preference": list(self.reviewer_preference),
        }


# --------------------------------------------------------------------------- #
# Durable assignment (requirement 6) + selection (requirement 19)             #
# --------------------------------------------------------------------------- #


class RoleSelectionError(Exception):
    def __init__(self, code: str, message: str, evidence: dict[str, str] | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.evidence = evidence or {}


@dataclass(frozen=True)
class AgentAssignment:
    implementer_id: str
    reviewer_id: str
    implementer_command: str
    reviewer_command: str
    mode: str
    reason: str
    sequence: int
    candidate_owner_id: str = ""
    availability: Mapping[str, str] = field(default_factory=dict)

    def to_record(self) -> dict[str, Any]:
        return {
            "implementerId": self.implementer_id,
            "reviewerId": self.reviewer_id,
            "implementerCommand": self.implementer_command,
            "reviewerCommand": self.reviewer_command,
            "mode": self.mode,
            "reason": self.reason,
            "sequence": self.sequence,
            "candidateOwnerId": self.candidate_owner_id or self.implementer_id,
            "availability": dict(self.availability),
        }

    @classmethod
    def from_record(cls, raw: Any) -> "AgentAssignment | None":
        if not isinstance(raw, Mapping):
            return None
        try:
            implementer_id = str(raw["implementerId"])
            return cls(
                implementer_id=implementer_id,
                reviewer_id=str(raw["reviewerId"]),
                implementer_command=str(raw["implementerCommand"]),
                reviewer_command=str(raw["reviewerCommand"]),
                mode=str(raw.get("mode", "adaptive")),
                reason=str(raw.get("reason", "")),
                sequence=int(raw.get("sequence", 1)),
                candidate_owner_id=str(raw.get("candidateOwnerId", implementer_id)),
                availability=dict(raw.get("availability", {})) if isinstance(raw.get("availability"), Mapping) else {},
            )
        except (KeyError, TypeError, ValueError):
            return None


def _role_state(availability: Mapping[str, Any] | None, agent_id: str, role: str) -> str:
    if not availability:
        return "available"
    entry = availability.get(agent_id)
    if isinstance(entry, Mapping):
        return str(entry.get(role, "available"))
    if isinstance(entry, str):
        return entry
    return "available"


def _first_available(order: Iterable[str], availability: Mapping[str, Any] | None, role: str) -> str | None:
    for agent_id in order:
        if _role_state(availability, agent_id, role) == "available":
            return agent_id
    return None


def _reason(
    mode: str,
    forced: bool,
    impl_order: list[str],
    implementer_id: str,
    reviewer_id: str,
    availability: Mapping[str, Any] | None,
) -> str:
    if forced:
        return f"operator preferred implementer={implementer_id}; reviewer={reviewer_id}"
    skipped = [
        f"{agent_id} {_role_state(availability, agent_id, 'implementer')}"
        for agent_id in impl_order
        if agent_id != implementer_id and _role_state(availability, agent_id, "implementer") != "available"
    ]
    if skipped:
        return f"{mode}: preferred implementer {', '.join(skipped)}; selected implementer={implementer_id} reviewer={reviewer_id}"
    return f"{mode} selection implementer={implementer_id} reviewer={reviewer_id}"


def select_assignment(
    *,
    policy: AgentRolePolicy,
    availability: Mapping[str, Any] | None = None,
    prefer_implementer: str | None = None,
    sequence: int = 1,
) -> AgentAssignment:
    """Deterministically choose an implementer / independent reviewer pair.

    Order: apply operator override, then policy preference, then role-specific
    availability, then enforce implementer != reviewer, and fail closed when no
    valid pair exists.
    """

    availability = availability or {}
    impl_order = list(policy.implementer_preference)
    forced = False
    if prefer_implementer:
        if prefer_implementer not in policy.agents:
            raise RoleSelectionError(
                "ROLE_OVERRIDE_UNKNOWN_AGENT",
                f"prefer-implementer '{prefer_implementer}' is not a configured agent",
                {"agent": prefer_implementer},
            )
        impl_order = [prefer_implementer] + [a for a in impl_order if a != prefer_implementer]
        forced = True

    if policy.mode == "fixed" and not forced:
        impl_order = impl_order[:1]

    implementer_id = _first_available(impl_order, availability, "implementer")
    if implementer_id is None:
        raise RoleSelectionError(
            "NO_ELIGIBLE_IMPLEMENTER",
            "no configured implementer is currently available",
            {"tried": ",".join(impl_order)},
        )

    reviewer_order = [r for r in policy.reviewer_preference if r != implementer_id]
    reviewer_id = _first_available(reviewer_order, availability, "reviewer")
    if reviewer_id is None:
        raise RoleSelectionError(
            "NO_INDEPENDENT_REVIEWER",
            "no independent reviewer is available; publication must block",
            {"implementer": implementer_id, "tried": ",".join(policy.reviewer_preference)},
        )

    return AgentAssignment(
        implementer_id=implementer_id,
        reviewer_id=reviewer_id,
        implementer_command=policy.agents[implementer_id],
        reviewer_command=policy.agents[reviewer_id],
        mode=policy.mode,
        reason=_reason(policy.mode, forced, impl_order, implementer_id, reviewer_id, availability),
        sequence=sequence,
        candidate_owner_id=implementer_id,
        availability={agent_id: _role_state(availability, agent_id, "implementer") for agent_id in policy.agents},
    )


def failover_implementer(
    *,
    policy: AgentRolePolicy,
    current: AgentAssignment,
    unavailable_ids: set[str],
    category: str,
) -> AgentAssignment:
    """Switch implementer after an external availability failure.

    Only permitted in adaptive mode. Fails closed if no alternate implementer
    remains or if the switch would leave no independent reviewer.
    """

    if policy.mode != "adaptive":
        raise RoleSelectionError(
            "FAILOVER_NOT_PERMITTED",
            "implementer failover requires adaptive agent-role mode",
            {"mode": policy.mode},
        )
    order = [a for a in policy.implementer_preference if a not in unavailable_ids]
    new_implementer = order[0] if order else None
    if new_implementer is None:
        raise RoleSelectionError(
            "NO_ELIGIBLE_IMPLEMENTER",
            "no alternate implementer remains after external availability failures",
            {"unavailable": ",".join(sorted(unavailable_ids)), "category": category},
        )
    reviewer_order = [r for r in policy.reviewer_preference if r != new_implementer]
    new_reviewer = reviewer_order[0] if reviewer_order else None
    if new_reviewer is None:
        raise RoleSelectionError(
            "NO_INDEPENDENT_REVIEWER",
            "implementer failover would leave no independent reviewer; publication must block",
            {"implementer": new_implementer},
        )
    availability = {agent_id: str(state) for agent_id, state in dict(current.availability).items()}
    for agent_id in unavailable_ids:
        availability[agent_id] = availability_state_for_category(category if agent_id == current.implementer_id else availability.get(agent_id, "unavailable"))
    return replace(
        current,
        implementer_id=new_implementer,
        reviewer_id=new_reviewer,
        implementer_command=policy.agents[new_implementer],
        reviewer_command=policy.agents[new_reviewer],
        reason=f"adaptive failover: {current.implementer_id} implementer {category}",
        sequence=current.sequence + 1,
        candidate_owner_id=new_implementer,
        availability=availability,
    )

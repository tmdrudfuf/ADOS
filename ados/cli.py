"""ADOS command-line interface."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

from .execution_policy import PolicyValidationError, load_execution_policy
from .primary_repository_guardian import PrimaryRepositoryGuardian


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="ados")
    subparsers = parser.add_subparsers(dest="area", required=True)

    policy_parser = subparsers.add_parser("policy")
    policy_subparsers = policy_parser.add_subparsers(dest="action", required=True)
    policy_validate = policy_subparsers.add_parser("validate")
    policy_validate.add_argument("--policy", required=True)

    guardian_parser = subparsers.add_parser("guardian")
    guardian_subparsers = guardian_parser.add_subparsers(dest="action", required=True)
    primary = guardian_subparsers.add_parser("primary")
    primary.add_argument("--policy", required=True)
    primary.add_argument("--repo", required=True)
    primary.add_argument("--expected-repository-path")
    primary.add_argument("--expected-branch")
    primary.add_argument("--expected-head")
    primary.add_argument("--allowed-local-path", action="append", default=[])

    args = parser.parse_args(argv)

    try:
        policy = load_execution_policy(args.policy)
    except PolicyValidationError as exc:
        _print_json({"status": "BLOCK", "violations": [exc.to_dict()]})
        return 2

    if args.area == "policy" and args.action == "validate":
        _print_json({"status": "PASS", "execution_policy": policy.to_dict()})
        return 0

    if args.area == "guardian" and args.action == "primary":
        result = PrimaryRepositoryGuardian().audit(
            policy=policy,
            repository_path=Path(args.repo),
            expected_repository_path=args.expected_repository_path,
            expected_branch=args.expected_branch,
            expected_head=args.expected_head,
            allowed_local_paths=args.allowed_local_path,
        )
        _print_json(result.to_dict())
        return 0 if result.status == "PASS" else 3

    parser.error("unsupported command")
    return 2


def _print_json(value: object) -> None:
    print(json.dumps(value, indent=2, sort_keys=True))


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))

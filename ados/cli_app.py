"""Reusable ADOS CLI application boundary."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from . import __version__
from .doctor import DoctorRequest, DoctorResult, DoctorService
from .execution_policy import PolicyValidationError, load_execution_policy
from .exact_head_gate import ExactHeadGate
from .primary_repository_guardian import PrimaryRepositoryGuardian
from .project_config import ProjectConfigError, load_project_config
from .review_engine import ReviewEngine, ReviewRequest
from .run_command import RunRequest, RunResult, RunService
from .status import StatusRequest, StatusResult, StatusService
from .validation_engine import ValidationEngine
from .worktree_lifecycle import WorktreeLifecycleEngine, WorktreeRequest


class CliApplication:
    def build_parser(self) -> argparse.ArgumentParser:
        parser = argparse.ArgumentParser(prog="ados")
        parser.add_argument("--version", action="version", version=f"ados {__version__}")
        subparsers = parser.add_subparsers(dest="area", required=True)

        doctor = subparsers.add_parser("doctor", help="diagnose ADOS project readiness")
        doctor.add_argument("project", nargs="?")
        doctor.add_argument("--project", dest="project_option")
        doctor.add_argument("--config")
        doctor.add_argument("--json", action="store_true")

        status = subparsers.add_parser("status", help="report ADOS project status")
        status.add_argument("project", nargs="?")
        status.add_argument("--project", dest="project_option")
        status.add_argument("--config")
        status.add_argument("--json", action="store_true")

        run = subparsers.add_parser("run", help="start an ADOS-managed feature run")
        run.add_argument("--project", required=True)
        run.add_argument("--feature", required=True)
        run.add_argument("--spec", type=int)
        run.add_argument("--config")
        run.add_argument("--dry-run", action="store_true")
        run.add_argument("--implementer-timeout-ms", type=int, default=300000)
        run.add_argument("--json", action="store_true")

        policy_parser = subparsers.add_parser("policy")
        policy_subparsers = policy_parser.add_subparsers(dest="action", required=True)
        policy_validate = policy_subparsers.add_parser("validate")
        policy_validate.add_argument("--policy", required=True)

        config_parser = subparsers.add_parser("config")
        config_subparsers = config_parser.add_subparsers(dest="action", required=True)
        config_validate = config_subparsers.add_parser("validate")
        config_validate.add_argument("--config", required=True)

        guardian_parser = subparsers.add_parser("guardian")
        guardian_subparsers = guardian_parser.add_subparsers(dest="action", required=True)
        primary = guardian_subparsers.add_parser("primary")
        primary.add_argument("--policy", required=True)
        primary.add_argument("--repo", required=True)
        primary.add_argument("--expected-repository-path")
        primary.add_argument("--expected-branch")
        primary.add_argument("--expected-head")
        primary.add_argument("--allowed-local-path", action="append", default=[])

        gate_parser = subparsers.add_parser("gate")
        gate_subparsers = gate_parser.add_subparsers(dest="action", required=True)
        exact_gate = gate_subparsers.add_parser("exact")
        exact_gate.add_argument("--repo", required=True)
        exact_gate.add_argument("--approved-review-sha", required=True)
        exact_gate.add_argument("--validated-sha", required=True)

        validation_parser = subparsers.add_parser("validation")
        validation_subparsers = validation_parser.add_subparsers(dest="action", required=True)
        validation_run = validation_subparsers.add_parser("run")
        validation_run.add_argument("--policy", required=True)
        validation_run.add_argument("--repo", required=True)

        review_parser = subparsers.add_parser("review")
        review_subparsers = review_parser.add_subparsers(dest="action", required=True)
        review_run = review_subparsers.add_parser("run")
        review_run.add_argument("--policy", required=True)
        review_run.add_argument("--repo", required=True)
        review_run.add_argument("--candidate-sha", required=True)
        review_run.add_argument("--base-sha", required=True)
        review_run.add_argument("--scope", required=True)
        review_run.add_argument("--diff", default="")

        worktree_parser = subparsers.add_parser("worktree")
        worktree_subparsers = worktree_parser.add_subparsers(dest="action", required=True)
        for action in ("create", "verify", "remove"):
            command = worktree_subparsers.add_parser(action)
            command.add_argument("--policy", required=True)
            command.add_argument("--primary-repo", required=True)
            command.add_argument("--worktree-path", required=True)
            command.add_argument("--branch", required=True)
            command.add_argument("--base-ref")
            command.add_argument("--expected-primary-branch")
            command.add_argument("--expected-primary-head")
            command.add_argument("--allowed-primary-local-path", action="append", default=[])

        return parser

    def run(self, argv: list[str] | None = None) -> int:
        parser = self.build_parser()
        args = parser.parse_args(argv)

        if args.area == "doctor":
            project = args.project_option or args.project
            if not project:
                parser.error("doctor requires --project <path> or a project path argument")
            result = DoctorService().run(
                DoctorRequest(
                    project_path=Path(project),
                    config_path=Path(args.config) if args.config else None,
                )
            )
            if args.json:
                print(json.dumps(result.to_dict(), indent=2, sort_keys=True))
            else:
                print(_format_doctor_human(result))
            return _doctor_exit_code(result)

        if args.area == "status":
            project = args.project_option or args.project
            if not project:
                parser.error("status requires --project <path> or a project path argument")
            result = StatusService().run(
                StatusRequest(
                    project_path=Path(project),
                    config_path=Path(args.config) if args.config else None,
                )
            )
            if args.json:
                print(json.dumps(result.to_dict(), indent=2, sort_keys=True))
            else:
                print(_format_status_human(result))
            return _status_exit_code(result)

        if args.area == "run":
            result = RunService().run(
                RunRequest(
                    project_path=Path(args.project),
                    feature_description=args.feature,
                    spec_number=args.spec,
                    config_path=Path(args.config) if args.config else None,
                    dry_run=args.dry_run,
                    implementer_timeout_ms=args.implementer_timeout_ms,
                )
            )
            if args.json:
                print(json.dumps(result.to_dict(), indent=2, sort_keys=True))
            else:
                print(_format_run_human(result))
            return _run_exit_code(result)

        if args.area == "config" and args.action == "validate":
            try:
                config = load_project_config(args.config)
            except ProjectConfigError as exc:
                _print_json({"status": "BLOCK", "violations": [exc.to_dict()]})
                return 2
            _print_json({"status": "PASS", "project_config": config.to_dict()})
            return 0

        try:
            policy = load_execution_policy(args.policy) if hasattr(args, "policy") else None
        except PolicyValidationError as exc:
            _print_json({"status": "BLOCK", "violations": [exc.to_dict()]})
            return 2

        if args.area == "gate" and args.action == "exact":
            result = ExactHeadGate().verify(
                repository_path=Path(args.repo),
                approved_review_sha=args.approved_review_sha,
                validated_sha=args.validated_sha,
            )
            _print_json(result.to_dict())
            return 0 if result.status == "MATCH" else 3

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

        if args.area == "validation" and args.action == "run":
            result = ValidationEngine().run(policy=policy, repository_path=Path(args.repo))
            _print_json(result.to_dict())
            return 0 if result.status == "PASS" else 3

        if args.area == "review" and args.action == "run":
            result = ReviewEngine().run(
                policy=policy,
                request=ReviewRequest(
                    repository_path=Path(args.repo),
                    candidate_sha=args.candidate_sha,
                    base_sha=args.base_sha,
                    scope=args.scope,
                    diff=args.diff,
                ),
            )
            _print_json(result.to_dict())
            return 0 if result.status == "PASS" else 3

        if args.area == "worktree":
            request = WorktreeRequest(
                primary_repository_path=Path(args.primary_repo),
                worktree_path=Path(args.worktree_path),
                branch=args.branch,
                base_ref=args.base_ref,
                expected_primary_branch=args.expected_primary_branch,
                expected_primary_head=args.expected_primary_head,
                allowed_primary_local_paths=tuple(args.allowed_primary_local_path),
            )
            engine = WorktreeLifecycleEngine()
            if args.action == "create":
                result = engine.create(policy=policy, request=request)
            elif args.action == "verify":
                result = engine.verify(policy=policy, request=request)
            elif args.action == "remove":
                result = engine.remove(policy=policy, request=request)
            else:
                parser.error("unsupported worktree command")
            _print_json(result.to_dict())
            return 0 if result.status == "PASS" else 3

        parser.error("unsupported command")
        return 2


def _print_json(value: object) -> None:
    print(json.dumps(value, indent=2, sort_keys=True))


def _format_doctor_human(result: DoctorResult) -> str:
    lines = ["ADOS Doctor", ""]
    for check in result.checks:
        mark = {"PASS": "[PASS]", "WARN": "[WARN]", "FAIL": "[FAIL]"}[check.status]
        lines.append(f"{mark} {check.summary}")
        for violation in check.violations:
            lines.append(f"  {violation.code}:")
            if violation.evidence:
                for key, value in sorted(violation.evidence.items()):
                    lines.append(f"  {key}: {value}")
            else:
                lines.append(f"  {violation.message}")
    lines.extend(("", result.status))
    return "\n".join(lines)


def _doctor_exit_code(result: DoctorResult) -> int:
    if result.status == "READY":
        return 0
    if result.status == "BLOCKED":
        return 1
    return 2


def _format_status_human(result: StatusResult) -> str:
    evidence = result.repository.evidence
    lines = [
        "ADOS Status",
        "",
        f"Project: {result.project.evidence.get('project_id', 'Unknown')}",
        f"Primary: {result.guardian.state}",
        f"Branch: {evidence.get('branch', 'Unknown')}",
        f"HEAD: {evidence.get('head', 'Unknown')}",
        "",
        f"Latest merged Spec: {result.spec.evidence.get('latest_merged_spec', 'Unknown')}",
        f"Active Spec: {result.spec.evidence.get('active_spec', 'None')}",
        f"Next Spec: {result.spec.evidence.get('next_unused_spec', 'Unknown')}",
        "",
        f"Validation: {result.validation.state}",
        f"Review: {result.review.state}",
        f"Exact HEAD Gate: {result.exact_head_gate.state}",
        f"Publication: {result.publication.state}",
        "",
        "Next action:",
        result.next_action.action,
        "",
        result.status,
    ]
    if result.recovery.reason_codes:
        lines.insert(-2, f"Reasons: {', '.join(result.recovery.reason_codes)}")
    return "\n".join(lines)


def _status_exit_code(result: StatusResult) -> int:
    if result.status == "INVALID":
        return 2
    if result.status == "BLOCKED":
        return 1
    return 0


def _format_run_human(result: RunResult) -> str:
    lines = ["ADOS Run", ""]
    if result.plan:
        lines.extend(
            [
                f"Spec: {result.plan.spec_number}",
                f"Branch: {result.plan.feature_branch}",
                f"Worktree: {result.plan.feature_worktree}",
                f"Base: {result.plan.authoritative_base_sha}",
                "",
            ]
        )
    if result.run_record:
        if result.resumed:
            lines.extend(["Resuming existing run:", f"run_id: {result.run_record.run_id}", f"status: {result.run_record.status}", "Implementer invocation starting...", ""])
        lines.extend(["Implementation handoff:", result.run_record.next_stage, ""])
    if result.eligibility.violations:
        lines.append("Violations:")
        for violation in result.eligibility.violations:
            lines.append(f"{violation.code}: {violation.message}")
        lines.append("")
    if result.eligibility.warnings:
        lines.append("Warnings:")
        for warning in result.eligibility.warnings:
            lines.append(f"{warning.code}: {warning.message}")
        lines.append("")
    lines.append(result.status)
    return "\n".join(lines)


def _run_exit_code(result: RunResult) -> int:
    if result.status == "INVALID":
        return 2
    if result.status in {"BLOCKED", "IMPLEMENTATION_FAILED", "IMPLEMENTATION_TIMED_OUT"}:
        return 1
    if result.status in {"PLANNED", "READY_FOR_IMPLEMENTATION", "READY_FOR_VALIDATION"}:
        return 0
    return 1

"""Deterministic tests for adaptive implementer / reviewer role selection."""

import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

from ados.agent_roles import (
    RUNTIME_FAILURE_CATEGORIES,
    AgentAssignment,
    AgentRolePolicy,
    RoleSelectionError,
    availability_state_for_category,
    classify_runtime_failure,
    failover_implementer,
    is_external_availability_failure,
    probe_agent,
    select_assignment,
)
from ados.cli_app import _format_run_human
from ados.execution_policy import ExecutionPolicy, PolicyValidationError
from ados.project_config import load_project_config
from ados.review_engine import ReviewEngine, ReviewRequest, ReviewResult, ReviewViolation
from ados.run_command import RunRequest, RunService
from ados.run_pipeline import RunPipeline, _is_transient_review_block, _review_independence_violation

from tests.test_cli_run import FakePublisher


ADAPTIVE_ROLES = {
    "mode": "adaptive",
    "agents": {"codex": "codex-cli exec", "claude": "claude-cli --print"},
    "implementer_preference": ["codex", "claude"],
    "reviewer_preference": ["claude", "codex"],
}


def _policy(roles):
    return AgentRolePolicy.from_mapping(roles)


# --------------------------------------------------------------------------- #
# Policy model + backward compatibility (acceptance A, B)                     #
# --------------------------------------------------------------------------- #


class AgentRolePolicyModelTests(unittest.TestCase):
    def base_execution_policy(self):
        return {
            "execution_policy": {
                "schema_version": "1",
                "publication": {"merge_strategy": "merge"},
                "review": {"reviewer": "claude-cli", "max_rounds": 5},
                "cleanup": {"autonomous": True},
                "guardian": {"stop_on_uncertain": True},
                "validation": {"commands": ["git diff --check"]},
            }
        }

    def test_absent_agent_roles_preserves_current_behavior(self):
        policy = ExecutionPolicy.from_mapping(self.base_execution_policy())
        self.assertIsNone(policy.agent_roles)
        self.assertIsNone(policy.to_dict()["agent_roles"])

    def test_fixed_and_adaptive_modes_parse(self):
        raw = self.base_execution_policy()
        raw["execution_policy"]["agent_roles"] = {**ADAPTIVE_ROLES, "mode": "fixed"}
        policy = ExecutionPolicy.from_mapping(raw)
        self.assertEqual("fixed", policy.agent_roles.mode)
        self.assertEqual(("codex", "claude"), policy.agent_roles.implementer_preference)
        roundtrip = json.loads(json.dumps(policy.to_dict()))
        self.assertEqual("fixed", roundtrip["agent_roles"]["mode"])
        self.assertEqual({"codex": "codex-cli exec", "claude": "claude-cli --print"}, roundtrip["agent_roles"]["agents"])

    def test_invalid_mode_agents_and_preferences_are_rejected(self):
        with self.assertRaises(PolicyValidationError) as ctx:
            _policy({**ADAPTIVE_ROLES, "mode": "auto"})
        self.assertEqual("POLICY_INVALID_AGENT_ROLES_MODE", ctx.exception.code)

        with self.assertRaises(PolicyValidationError) as ctx:
            _policy({**ADAPTIVE_ROLES, "agents": {}})
        self.assertEqual("POLICY_INVALID_AGENT_ROLES_AGENTS", ctx.exception.code)

        with self.assertRaises(PolicyValidationError) as ctx:
            _policy({**ADAPTIVE_ROLES, "implementer_preference": ["ghost"]})
        self.assertEqual("POLICY_INVALID_AGENT_ROLES_IMPLEMENTER_PREFERENCE", ctx.exception.code)


# --------------------------------------------------------------------------- #
# Runtime failure classification (acceptance P, Q, R, S)                      #
# --------------------------------------------------------------------------- #


class ClassificationTests(unittest.TestCase):
    def test_known_external_categories(self):
        self.assertEqual("QUOTA_EXHAUSTED", classify_runtime_failure(stderr="Error: quota exceeded for this org"))
        self.assertEqual("USAGE_LIMIT_REACHED", classify_runtime_failure(stdout="You have hit your usage limit"))
        self.assertEqual("CAPACITY_UNAVAILABLE", classify_runtime_failure(stderr="server is overloaded, try again later"))
        self.assertEqual("COMMAND_NOT_FOUND", classify_runtime_failure(executable_found=False))
        self.assertEqual("TRANSIENT_RUNTIME_UNAVAILABLE", classify_runtime_failure(stderr="connection reset by peer"))

    def test_oauth_session_expired_is_authentication_not_code_failure(self):
        category = classify_runtime_failure(stderr="OAuth session expired and could not be refreshed")
        self.assertEqual("AUTHENTICATION_UNAVAILABLE", category)
        self.assertTrue(is_external_availability_failure(category))

    def test_capacity_failure_classified_separately_from_code_failure(self):
        self.assertEqual("CAPACITY_UNAVAILABLE", classify_runtime_failure(exit_code=1, stderr="503 service unavailable"))
        self.assertFalse(is_external_availability_failure("UNKNOWN_RUNTIME_FAILURE"))

    def test_unknown_runtime_error_fails_conservatively(self):
        for text in ("AssertionError: expected 3 got 4", "npm ERR! Test failed.  See above for more details.", "tsc: error TS2322: Type 'x'"):
            self.assertEqual("UNKNOWN_RUNTIME_FAILURE", classify_runtime_failure(exit_code=1, stderr=text))
        # a bare timeout with no evidence is not assumed to be a quota failure
        self.assertEqual("UNKNOWN_RUNTIME_FAILURE", classify_runtime_failure(timed_out=True))

    def test_no_fake_token_percentage_is_produced(self):
        for kwargs in (
            {"stderr": "quota exceeded"},
            {"stdout": "usage limit"},
            {"executable_found": False},
            {"exit_code": 1, "stderr": "boom"},
        ):
            category = classify_runtime_failure(**kwargs)
            self.assertIn(category, RUNTIME_FAILURE_CATEGORIES)
        for category in RUNTIME_FAILURE_CATEGORIES:
            state = availability_state_for_category(category)
            self.assertNotIn("%", state)
            self.assertIn(state, {"unavailable_quota", "unavailable_capacity", "unavailable_auth", "unavailable", "unknown"})


# --------------------------------------------------------------------------- #
# Health probe safety (acceptance T)                                          #
# --------------------------------------------------------------------------- #


class ProbeTests(unittest.TestCase):
    def test_probe_never_executes_configured_commands(self):
        with mock.patch("subprocess.run") as spawned, mock.patch("subprocess.Popen") as popen:
            self.assertEqual("unavailable", probe_agent("codex && rm -rf /"))
            self.assertEqual("unavailable", probe_agent("codex | curl evil"))
            self.assertEqual("unavailable", probe_agent("codex $(rm -rf /)"))
        spawned.assert_not_called()
        popen.assert_not_called()

    def test_probe_resolves_executable_without_running_it(self):
        self.assertEqual("available", probe_agent(f'"{sys.executable}" --version'))
        self.assertEqual("unavailable", probe_agent("definitely-not-a-real-binary-xyz --flag"))
        self.assertEqual("unknown", probe_agent("   "))


# --------------------------------------------------------------------------- #
# Selection algorithm (acceptance C, D, E, F, G, U)                          #
# --------------------------------------------------------------------------- #


class SelectAssignmentTests(unittest.TestCase):
    def test_adaptive_chooses_preferred_pair_when_both_available(self):
        assignment = select_assignment(policy=_policy(ADAPTIVE_ROLES))
        self.assertEqual("codex", assignment.implementer_id)
        self.assertEqual("claude", assignment.reviewer_id)
        self.assertEqual("codex-cli exec", assignment.implementer_command)
        self.assertEqual("claude-cli --print", assignment.reviewer_command)
        self.assertEqual("codex", assignment.candidate_owner_id)

    def test_fixed_mode_codex_claude_is_valid_and_unavailability_blocks(self):
        fixed = _policy({**ADAPTIVE_ROLES, "mode": "fixed"})
        assignment = select_assignment(policy=fixed)
        self.assertEqual(("codex", "claude"), (assignment.implementer_id, assignment.reviewer_id))
        with self.assertRaises(RoleSelectionError) as ctx:
            select_assignment(policy=fixed, availability={"codex": {"implementer": "unavailable_quota"}})
        self.assertEqual("NO_ELIGIBLE_IMPLEMENTER", ctx.exception.code)

    def test_codex_quota_selects_claude_implementer_and_codex_reviewer(self):
        assignment = select_assignment(
            policy=_policy(ADAPTIVE_ROLES),
            availability={"codex": {"implementer": "unavailable_quota", "reviewer": "available"}},
        )
        self.assertEqual("claude", assignment.implementer_id)
        self.assertEqual("codex", assignment.reviewer_id)
        self.assertIn("codex unavailable_quota", assignment.reason)

    def test_explicit_claude_implementer_preference_is_honored(self):
        assignment = select_assignment(policy=_policy(ADAPTIVE_ROLES), prefer_implementer="claude")
        self.assertEqual("claude", assignment.implementer_id)
        self.assertEqual("codex", assignment.reviewer_id)
        self.assertIn("operator preferred", assignment.reason)
        with self.assertRaises(RoleSelectionError) as ctx:
            select_assignment(policy=_policy(ADAPTIVE_ROLES), prefer_implementer="gemini")
        self.assertEqual("ROLE_OVERRIDE_UNKNOWN_AGENT", ctx.exception.code)

    def test_same_agent_cannot_implement_and_review(self):
        roles = {**ADAPTIVE_ROLES, "reviewer_preference": ["codex"]}
        with self.assertRaises(RoleSelectionError) as ctx:
            select_assignment(policy=_policy(roles))
        self.assertEqual("NO_INDEPENDENT_REVIEWER", ctx.exception.code)

    def test_no_independent_reviewer_blocks(self):
        with self.assertRaises(RoleSelectionError) as ctx:
            select_assignment(
                policy=_policy(ADAPTIVE_ROLES),
                availability={
                    "codex": {"implementer": "available", "reviewer": "unavailable_capacity"},
                    "claude": {"implementer": "available", "reviewer": "unavailable_auth"},
                },
            )
        self.assertEqual("NO_INDEPENDENT_REVIEWER", ctx.exception.code)


class FailoverTests(unittest.TestCase):
    def test_adaptive_failover_switches_implementer_and_preserves_independence(self):
        current = select_assignment(policy=_policy(ADAPTIVE_ROLES))
        switched = failover_implementer(
            policy=_policy(ADAPTIVE_ROLES),
            current=current,
            unavailable_ids={"codex"},
            category="QUOTA_EXHAUSTED",
        )
        self.assertEqual("claude", switched.implementer_id)
        self.assertEqual("codex", switched.reviewer_id)
        self.assertEqual("claude", switched.candidate_owner_id)
        self.assertEqual(current.sequence + 1, switched.sequence)
        self.assertIn("QUOTA_EXHAUSTED", switched.reason)

    def test_failover_requires_adaptive_mode(self):
        fixed = _policy({**ADAPTIVE_ROLES, "mode": "fixed"})
        with self.assertRaises(RoleSelectionError) as ctx:
            failover_implementer(policy=fixed, current=select_assignment(policy=fixed), unavailable_ids={"codex"}, category="QUOTA_EXHAUSTED")
        self.assertEqual("FAILOVER_NOT_PERMITTED", ctx.exception.code)

    def test_failover_fails_closed_when_no_alternate_remains(self):
        current = select_assignment(policy=_policy(ADAPTIVE_ROLES))
        with self.assertRaises(RoleSelectionError) as ctx:
            failover_implementer(policy=_policy(ADAPTIVE_ROLES), current=current, unavailable_ids={"codex", "claude"}, category="QUOTA_EXHAUSTED")
        self.assertEqual("NO_ELIGIBLE_IMPLEMENTER", ctx.exception.code)


class AgentAssignmentRecordTests(unittest.TestCase):
    def test_record_roundtrip_and_no_secrets_persisted(self):
        assignment = select_assignment(policy=_policy(ADAPTIVE_ROLES))
        record = assignment.to_record()
        self.assertEqual(
            {"implementerId", "reviewerId", "implementerCommand", "reviewerCommand", "mode", "reason", "sequence", "candidateOwnerId", "availability"},
            set(record),
        )
        for key in record:
            self.assertNotIn("token", key.lower())
            self.assertNotIn("secret", key.lower())
            self.assertNotIn("apikey", key.lower().replace("_", ""))
        restored = AgentAssignment.from_record(json.loads(json.dumps(record)))
        self.assertEqual(assignment.implementer_id, restored.implementer_id)
        self.assertEqual(assignment.reviewer_command, restored.reviewer_command)
        self.assertIsNone(AgentAssignment.from_record(None))


# --------------------------------------------------------------------------- #
# Reviewer command plumbing (acceptance E)                                    #
# --------------------------------------------------------------------------- #


class ReviewerCommandTests(unittest.TestCase):
    def _policy_doc(self):
        return ExecutionPolicy.from_mapping(
            {
                "execution_policy": {
                    "schema_version": "1",
                    "publication": {"merge_strategy": "merge"},
                    "review": {"reviewer": "policy-default-reviewer", "max_rounds": 5},
                    "cleanup": {"autonomous": True},
                    "guardian": {"stop_on_uncertain": True},
                    "validation": {"commands": ["git diff --check"]},
                }
            }
        )

    def test_assignment_reviewer_command_overrides_policy_reviewer(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp) / "repo"
            repo.mkdir()
            subprocess.run(("git", "init", "-b", "main"), cwd=repo, check=True, capture_output=True)
            subprocess.run(("git", "config", "user.email", "t@t.invalid"), cwd=repo, check=True, capture_output=True)
            subprocess.run(("git", "config", "user.name", "T"), cwd=repo, check=True, capture_output=True)
            (repo / "a.txt").write_text("base\n", encoding="utf-8")
            subprocess.run(("git", "add", "."), cwd=repo, check=True, capture_output=True)
            subprocess.run(("git", "commit", "-m", "base"), cwd=repo, check=True, capture_output=True)
            base = subprocess.run(("git", "rev-parse", "HEAD"), cwd=repo, check=True, capture_output=True, text=True).stdout.strip()
            (repo / "a.txt").write_text("candidate\n", encoding="utf-8")
            subprocess.run(("git", "commit", "-am", "candidate"), cwd=repo, check=True, capture_output=True)
            candidate = subprocess.run(("git", "rev-parse", "HEAD"), cwd=repo, check=True, capture_output=True, text=True).stdout.strip()

            with mock.patch("ados.review_engine.subprocess.run") as spawned:
                def fake_run(args, **kwargs):
                    if args[0] == "git":
                        return subprocess.CompletedProcess(args, 0, "ok", "")
                    return subprocess.CompletedProcess(args, 0, "Approved", "")

                spawned.side_effect = fake_run
                result = ReviewEngine().run(
                    policy=self._policy_doc(),
                    request=ReviewRequest(
                        repository_path=repo,
                        candidate_sha=candidate,
                        base_sha=base,
                        scope="a.txt",
                        reviewer_command="selected-codex-reviewer --json",
                    ),
                )

        reviewer_call = [call for call in spawned.call_args_list if call.args[0][0] != "git"][0]
        self.assertEqual("selected-codex-reviewer", reviewer_call.args[0][0])
        self.assertEqual("PASS", result.status)


# --------------------------------------------------------------------------- #
# End-to-end durable assignment / failover (acceptance H, I, J, K, L, M,     #
# N, O, V, X)                                                                 #
# --------------------------------------------------------------------------- #


# A fake agent CLI: one command that reviews when handed a review prompt and
# otherwise runs the given implementer body. Mirrors how a real agent CLI is a
# single command used for both roles.
_AGENT_TEMPLATE = (
    "import sys, uuid\n"
    "from pathlib import Path\n"
    "prompt = sys.stdin.read()\n"
    "if 'Review exact candidate HEAD' in prompt:\n"
    "    print('Approved')\n"
    "    sys.exit(0)\n"
    "{body}\n"
)
_BODY_SUCCESS = (
    "p = Path('implementation.txt')\n"
    # write raw LF bytes so re-runs produce a clean delta on every platform
    "p.write_bytes((p.read_bytes() if p.exists() else b'') + uuid.uuid4().hex.encode() + b'\\n')\n"
    "print('implemented')\n"
)
_BODY_QUOTA = "print('Error: usage limit reached; please try again later', file=sys.stderr)\nsys.exit(1)\n"
_BODY_QUOTA_DIRTY = (
    "Path('half-done.txt').write_text('partial work', encoding='utf-8')\n"
    "print('quota exceeded mid-run', file=sys.stderr)\n"
    "sys.exit(1)\n"
)


def _agent_script(body):
    return _AGENT_TEMPLATE.format(body=body)


IMPL_SUCCESS = _agent_script(_BODY_SUCCESS)
IMPL_QUOTA = _agent_script(_BODY_QUOTA)
IMPL_QUOTA_DIRTY = _agent_script(_BODY_QUOTA_DIRTY)


class _RoleProject:
    def __init__(self, test, *, codex_script, claude_script, mode="adaptive", validation_commands=None):
        self.test = test
        self.codex_script = codex_script
        self.claude_script = claude_script
        self.mode = mode
        self.validation_commands = validation_commands or ["git diff --check"]

    def __enter__(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.repo = self.root / "project"
        self.repo.mkdir(parents=True)
        g = self.test.git
        g(self.repo, "init", "-b", "main")
        g(self.repo, "config", "user.email", "test@example.invalid")
        g(self.repo, "config", "user.name", "Test User")
        (self.repo / ".gitignore").write_text(".agent-workflow/\n", encoding="utf-8")
        (self.repo / "README.md").write_text("base\n", encoding="utf-8")
        g(self.repo, "add", ".gitignore", "README.md")
        g(self.repo, "commit", "-m", "initial")
        g(self.repo, "remote", "add", "origin", str(self.repo))
        g(self.repo, "update-ref", "refs/remotes/origin/main", self.test.head(self.repo))

        codex = self.root / "codex_agent.py"
        claude = self.root / "claude_agent.py"
        codex.write_text(self.codex_script, encoding="utf-8")
        claude.write_text(self.claude_script, encoding="utf-8")
        self.codex_cmd = f'"{sys.executable}" "{codex}"'
        self.claude_cmd = f'"{sys.executable}" "{claude}"'
        config = {
            "project": {
                "id": "role-project",
                "primary_repository_path": str(self.repo),
                "default_branch": "main",
                "allowed_primary_local_paths": [],
            },
            "roles": {"implementer": self.codex_cmd, "reviewer": self.claude_cmd},
            "execution_policy": {
                "schema_version": "1",
                "publication": {"merge_strategy": "merge"},
                "review": {"reviewer": self.claude_cmd, "max_rounds": 5},
                "cleanup": {"autonomous": True},
                "guardian": {"stop_on_uncertain": True},
                "validation": {"commands": self.validation_commands},
                "agent_roles": {
                    "mode": self.mode,
                    "agents": {"codex": self.codex_cmd, "claude": self.claude_cmd},
                    "implementer_preference": ["codex", "claude"],
                    "reviewer_preference": ["claude", "codex"],
                },
            },
        }
        self.config = self.root / "project-config.json"
        self.config.write_text(json.dumps(config), encoding="utf-8")
        return self

    def __exit__(self, *exc):
        self.temp.cleanup()

    def record(self):
        matches = list(self.root.rglob("ados-run.json"))
        assert matches, f"no run record under {self.root}"
        # Prefer the most recently written copy.
        return json.loads(max(matches, key=lambda p: p.stat().st_mtime).read_text(encoding="utf-8"))


class DurableAssignmentIntegrationTests(unittest.TestCase):
    def git(self, repo, *args):
        return subprocess.run(("git", *args), cwd=repo, check=True, capture_output=True, text=True)

    def head(self, repo):
        return self.git(repo, "rev-parse", "HEAD").stdout.strip()

    def _service(self, fixture):
        return RunService(pipeline=RunPipeline(publisher=FakePublisher(fixture.repo)))

    def _run(self, fixture, **kwargs):
        return self._service(fixture).run(RunRequest(fixture.repo, "Adaptive role selection", None, fixture.config, **kwargs))

    def test_adaptive_preferred_pair_is_persisted_and_resume_preserves_it(self):
        with _RoleProject(self, codex_script=IMPL_SUCCESS, claude_script=IMPL_SUCCESS) as fixture:
            # Plain RunService blocks at publication, leaving the durable run intact.
            first = RunService().run(RunRequest(fixture.repo, "Adaptive role selection", None, fixture.config))
            record = fixture.record()
            second = RunService().run(RunRequest(fixture.repo, "Adaptive role selection", None, fixture.config))
            resumed_record = fixture.record()

        self.assertEqual("READY_FOR_PUBLICATION", first.status)
        self.assertEqual("codex", record["agentAssignment"]["implementerId"])
        self.assertEqual("claude", record["agentAssignment"]["reviewerId"])
        self.assertEqual("adaptive", record["agentAssignment"]["mode"])
        self.assertEqual("codex", record["agentAssignment"]["candidateOwnerId"])
        self.assertTrue(second.resumed)
        self.assertEqual("READY_FOR_PUBLICATION", second.status)
        self.assertEqual(record["agentAssignment"], resumed_record["agentAssignment"])
        self.assertIn("Agent assignment:", _format_run_human(second))
        self.assertIn("Implementer: codex", _format_run_human(second))

    def test_explicit_claude_preference_is_recorded_durably(self):
        with _RoleProject(self, codex_script=IMPL_SUCCESS, claude_script=IMPL_SUCCESS) as fixture:
            result = self._run(fixture, prefer_implementer="claude")
            record = fixture.record()

        self.assertIn(result.status, {"COMPLETE", "READY_FOR_PUBLICATION"})
        self.assertEqual("claude", record["agentAssignment"]["implementerId"])
        self.assertEqual("codex", record["agentAssignment"]["reviewerId"])
        self.assertIn("operator preferred", record["agentAssignment"]["reason"])

    def test_external_quota_failover_does_not_burn_recovery_budget(self):
        with _RoleProject(self, codex_script=IMPL_QUOTA, claude_script=IMPL_SUCCESS) as fixture:
            result = self._run(fixture)
            record = fixture.record()
            stages = [stage.id for stage in result.pipeline_result.stages]

        self.assertEqual("COMPLETE", result.status)
        self.assertEqual("claude", record["agentAssignment"]["implementerId"])
        self.assertEqual("codex", record["agentAssignment"]["reviewerId"])
        self.assertEqual("claude", record["agentAssignment"]["candidateOwnerId"])
        events = record.get("agentAvailabilityEvents", [])
        self.assertEqual(1, len(events))
        self.assertEqual("codex", events[0]["agentId"])
        self.assertEqual("USAGE_LIMIT_REACHED", events[0]["category"])
        self.assertEqual([], record.get("implementationRecoveryAttempts", []))
        self.assertIn("implementer_failover", stages)
        # exact-HEAD / publication invariants remain: reviewed == validated == merged
        self.assertEqual(
            result.pipeline_result.validation.head_after,
            result.pipeline_result.review.reviewed_sha,
        )
        self.assertEqual("MATCH", result.pipeline_result.exact_head_gate["status"])

    def test_dirty_worktree_failover_blocks_safely(self):
        with _RoleProject(self, codex_script=IMPL_QUOTA_DIRTY, claude_script=IMPL_SUCCESS) as fixture:
            result = self._run(fixture)
            record = fixture.record()
            worktree = Path(record["featureWorktree"])
            partial_work_preserved = (worktree / "half-done.txt").exists()

        self.assertEqual("IMPLEMENTATION_FAILED", result.status)
        codes = {v.code for v in result.pipeline_result.violations}
        self.assertIn("FAILOVER_DIRTY_WORKTREE", codes)
        self.assertEqual("codex", record["agentAssignment"]["implementerId"])
        self.assertTrue(partial_work_preserved)
        self.assertEqual([], record.get("implementationRecoveryAttempts", []))

    def test_validation_failure_does_not_trigger_agent_failover(self):
        counter = "vcount.txt"
        validation_script = (
            "from pathlib import Path\n"
            "import sys\n"
            f"c = Path(r'@COUNTER@')\n"
            "n = int(c.read_text()) if c.exists() else 0\n"
            "c.write_text(str(n + 1))\n"
            "sys.exit(1 if n == 0 else 0)\n"
        )

        with _RoleProject(self, codex_script=IMPL_SUCCESS, claude_script=IMPL_SUCCESS) as fixture:
            vfile = fixture.root / counter
            script_path = fixture.root / "vcheck.py"
            script_path.write_text(validation_script.replace("@COUNTER@", str(vfile)), encoding="utf-8")
            raw = json.loads(fixture.config.read_text(encoding="utf-8"))
            raw["execution_policy"]["validation"]["commands"] = [f'"{sys.executable}" "{script_path}"']
            fixture.config.write_text(json.dumps(raw), encoding="utf-8")

            result = self._run(fixture)
            record = fixture.record()
            stages = [stage.id for stage in result.pipeline_result.stages]

        self.assertIn(result.status, {"COMPLETE", "READY_FOR_PUBLICATION"})
        self.assertEqual("codex", record["agentAssignment"]["implementerId"])
        self.assertEqual([], record.get("agentAvailabilityEvents", []))
        self.assertNotIn("implementer_failover", stages)
        self.assertIn("validation_recovery_implementer", stages)

    def test_external_failure_after_candidate_produced_blocks_without_switching(self):
        with _RoleProject(self, codex_script=IMPL_SUCCESS, claude_script=IMPL_SUCCESS) as fixture:
            counter = fixture.root / "ccount.txt"
            codex = fixture.root / "codex_agent.py"
            codex.write_text(
                "import sys, uuid\n"
                "from pathlib import Path\n"
                "prompt = sys.stdin.read()\n"
                "if 'Review exact candidate HEAD' in prompt:\n"
                "    print('Approved'); sys.exit(0)\n"
                f"c = Path(r'{counter}')\n"
                "n = int(c.read_text()) if c.exists() else 0\n"
                "c.write_text(str(n + 1))\n"
                "if n == 0:\n"
                "    p = Path('implementation.txt')\n"
                "    p.write_bytes((p.read_bytes() if p.exists() else b'') + uuid.uuid4().hex.encode() + b'\\n')\n"
                "    print('implemented'); sys.exit(0)\n"
                "print('Error: quota exceeded for this account', file=sys.stderr)\n"
                "sys.exit(1)\n",
                encoding="utf-8",
            )
            reviewer = fixture.root / "cr_reviewer.py"
            reviewer.write_text("print('Changes Requested')\n", encoding="utf-8")
            raw = json.loads(fixture.config.read_text(encoding="utf-8"))
            raw["execution_policy"]["agent_roles"]["agents"]["claude"] = f'"{sys.executable}" "{reviewer}"'
            raw["execution_policy"]["review"]["reviewer"] = f'"{sys.executable}" "{reviewer}"'
            fixture.config.write_text(json.dumps(raw), encoding="utf-8")

            result = self._run(fixture)
            record = fixture.record()
            worktree = Path(record["featureWorktree"])
            candidate_preserved = (worktree / "implementation.txt").exists()

        self.assertEqual("IMPLEMENTATION_FAILED", result.status)
        codes = {v.code for v in result.pipeline_result.violations}
        self.assertIn("FAILOVER_CANDIDATE_ALREADY_PRODUCED", codes)
        self.assertEqual("codex", record["agentAssignment"]["implementerId"])
        self.assertEqual("codex", record["agentAssignment"]["candidateOwnerId"])
        self.assertTrue(candidate_preserved)
        self.assertEqual([], record.get("implementationRecoveryAttempts", []))
        self.assertEqual(1, len(record.get("agentAvailabilityEvents", [])))

    def test_changes_requested_does_not_switch_implementer(self):
        with _RoleProject(self, codex_script=IMPL_SUCCESS, claude_script=IMPL_SUCCESS) as fixture:
            reviewer = fixture.root / "reviewer_rounds.py"
            reviewer.write_text(
                "from pathlib import Path\n"
                f"c = Path(r'{fixture.root / 'rcount.txt'}')\n"
                "n = int(c.read_text()) if c.exists() else 0\n"
                "c.write_text(str(n + 1))\n"
                "print('Changes Requested' if n == 0 else 'Approved')\n",
                encoding="utf-8",
            )
            raw = json.loads(fixture.config.read_text(encoding="utf-8"))
            reviewer_cmd = f'"{sys.executable}" "{reviewer}"'
            raw["execution_policy"]["review"]["reviewer"] = reviewer_cmd
            raw["execution_policy"]["agent_roles"]["agents"]["claude"] = reviewer_cmd
            # codex stays the implementer; claude id now maps to the rounds reviewer
            fixture.config.write_text(json.dumps(raw), encoding="utf-8")

            result = self._run(fixture)
            record = fixture.record()
            stages = [stage.id for stage in result.pipeline_result.stages]

        self.assertIn(result.status, {"COMPLETE", "READY_FOR_PUBLICATION"})
        self.assertEqual("codex", record["agentAssignment"]["implementerId"])
        self.assertEqual([], record.get("agentAvailabilityEvents", []))
        self.assertNotIn("implementer_failover", stages)
        self.assertIn("implementer_fix", stages)


# --------------------------------------------------------------------------- #
# Blocking finding 1: publication-resume implementer/reviewer independence     #
# --------------------------------------------------------------------------- #


def _review_block(*violations):
    return ReviewResult("BLOCK", "Unavailable", "sha", 1, "", "", tuple(violations))


class PublicationResumeIndependenceTests(unittest.TestCase):
    def test_independent_durable_assignment_permits_publication(self):
        record = {"agentAssignment": {"reviewerId": "claude", "candidateOwnerId": "codex", "reviewerCommand": "claude-cli --print"}}
        self.assertIsNone(_review_independence_violation(record, adaptive_roles=True))

    def test_reviewer_equal_to_candidate_owner_is_blocked(self):
        record = {"agentAssignment": {"reviewerId": "codex", "candidateOwnerId": "codex", "reviewerCommand": "codex exec"}}
        violation = _review_independence_violation(record, adaptive_roles=True)
        self.assertIsNotNone(violation)
        self.assertEqual("REVIEWER_NOT_INDEPENDENT", violation.code)

    def test_missing_reviewer_or_owner_identity_fails_closed(self):
        # empty reviewer command
        v1 = _review_independence_violation({"agentAssignment": {"reviewerId": "claude", "candidateOwnerId": "codex", "reviewerCommand": ""}}, adaptive_roles=True)
        self.assertEqual("NO_INDEPENDENT_REVIEWER", v1.code)
        # ambiguous ownership: assignment present but no owner and no implementer id
        v2 = _review_independence_violation({"agentAssignment": {"reviewerId": "claude", "reviewerCommand": "claude-cli --print"}}, adaptive_roles=True)
        self.assertEqual("NO_INDEPENDENT_REVIEWER", v2.code)
        # no reviewer id
        v3 = _review_independence_violation({"agentAssignment": {"candidateOwnerId": "codex", "reviewerCommand": "claude-cli --print"}}, adaptive_roles=True)
        self.assertEqual("NO_INDEPENDENT_REVIEWER", v3.code)

    def test_absent_or_corrupt_assignment_fails_closed_under_adaptive_roles(self):
        # An adaptive-role run always persists an agentAssignment; an absent,
        # None, or non-dict one means the durable reviewer / candidate-owner
        # identity is missing or corrupted, so publication must block. Old
        # Approved + exact-HEAD evidence must not buy a bypass here.
        for corrupt in ({}, {"agentAssignment": None}, {"agentAssignment": "wiped"}, {"agentAssignment": []}):
            violation = _review_independence_violation(corrupt, adaptive_roles=True)
            self.assertIsNotNone(violation, corrupt)
            self.assertEqual("NO_INDEPENDENT_REVIEWER", violation.code, corrupt)

    def test_absent_assignment_preserves_genuine_pre_adaptive_legacy_publication(self):
        # A genuine legacy run has no agent_roles policy (adaptive_roles=False)
        # and never had an assignment; the historical fixed-reviewer path stays
        # publishable. This is the only compatibility carve-out.
        self.assertIsNone(_review_independence_violation({}, adaptive_roles=False))


class _RoleRunMixin:
    """Integration helpers for driving RunService against a _RoleProject fixture.

    A plain mixin (not a TestCase) so subclasses do not re-run the base
    DurableAssignmentIntegrationTests suite.
    """

    def git(self, repo, *args):
        return subprocess.run(("git", *args), cwd=repo, check=True, capture_output=True, text=True)

    def head(self, repo):
        return self.git(repo, "rev-parse", "HEAD").stdout.strip()

    def _run(self, fixture, **kwargs):
        service = RunService(pipeline=RunPipeline(publisher=FakePublisher(fixture.repo)))
        return service.run(RunRequest(fixture.repo, "Adaptive role selection", None, fixture.config, **kwargs))

    def _durable_path(self, fixture):
        return max(fixture.root.rglob("ados-run.json"), key=lambda p: p.stat().st_mtime)


class PublicationResumeIndependenceIntegrationTests(_RoleRunMixin, unittest.TestCase):
    def test_resume_blocks_when_reviewer_is_candidate_owner_even_with_exact_head_match(self):
        with _RoleProject(self, codex_script=IMPL_SUCCESS, claude_script=IMPL_SUCCESS) as fixture:
            first = RunService().run(RunRequest(fixture.repo, "Adaptive role selection", None, fixture.config))
            self.assertEqual("READY_FOR_PUBLICATION", first.status)

            path = self._durable_path(fixture)
            record = json.loads(path.read_text(encoding="utf-8"))
            # exact-HEAD evidence is untouched; only the durable reviewer identity
            # is tampered to match the candidate owner.
            record["agentAssignment"]["reviewerId"] = record["agentAssignment"]["candidateOwnerId"]
            path.write_text(json.dumps(record), encoding="utf-8")

            blocked = RunService().run(RunRequest(fixture.repo, "Adaptive role selection", None, fixture.config))
            blocked_record = json.loads(self._durable_path(fixture).read_text(encoding="utf-8"))

        self.assertEqual("REVIEW_BLOCKED", blocked.status)
        self.assertIn("REVIEWER_NOT_INDEPENDENT", {v.code for v in blocked.pipeline_result.violations})
        self.assertEqual("REVIEW_BLOCKED", blocked_record["status"])

    def test_resume_blocks_when_agent_assignment_is_missing_even_with_exact_head_match(self):
        with _RoleProject(self, codex_script=IMPL_SUCCESS, claude_script=IMPL_SUCCESS) as fixture:
            first = RunService().run(RunRequest(fixture.repo, "Adaptive role selection", None, fixture.config))
            self.assertEqual("READY_FOR_PUBLICATION", first.status)

            path = self._durable_path(fixture)
            record = json.loads(path.read_text(encoding="utf-8"))
            # Approved review + exact-HEAD evidence stay intact; only the durable
            # agent assignment (reviewer / candidate-owner identity proof) is
            # removed, as it would be by corruption of an adaptive-role record.
            record.pop("agentAssignment")
            path.write_text(json.dumps(record), encoding="utf-8")

            blocked = RunService().run(RunRequest(fixture.repo, "Adaptive role selection", None, fixture.config))
            blocked_record = json.loads(self._durable_path(fixture).read_text(encoding="utf-8"))

        self.assertTrue(blocked.resumed)
        self.assertEqual("REVIEW_BLOCKED", blocked.status)
        self.assertIn("NO_INDEPENDENT_REVIEWER", {v.code for v in blocked.pipeline_result.violations})
        self.assertEqual("REVIEW_BLOCKED", blocked_record["status"])

    def test_normal_independent_resume_still_publishes(self):
        with _RoleProject(self, codex_script=IMPL_SUCCESS, claude_script=IMPL_SUCCESS) as fixture:
            first = RunService().run(RunRequest(fixture.repo, "Adaptive role selection", None, fixture.config))
            second = RunService().run(RunRequest(fixture.repo, "Adaptive role selection", None, fixture.config))

        self.assertEqual("READY_FOR_PUBLICATION", first.status)
        self.assertTrue(second.resumed)
        self.assertEqual("READY_FOR_PUBLICATION", second.status)

    def test_fresh_publication_path_is_unaffected(self):
        with _RoleProject(self, codex_script=IMPL_SUCCESS, claude_script=IMPL_SUCCESS) as fixture:
            result = self._run(fixture)
        self.assertIn(result.status, {"COMPLETE", "READY_FOR_PUBLICATION"})


# --------------------------------------------------------------------------- #
# Blocking finding 2: deterministic reviewer runtime failure classification    #
# --------------------------------------------------------------------------- #


class ReviewerRuntimeClassificationTests(unittest.TestCase):
    def _run_reviewer(self, *, stdout="", stderr="", returncode=1):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp) / "repo"
            repo.mkdir()
            for args in (("init", "-b", "main"), ("config", "user.email", "t@t.invalid"), ("config", "user.name", "T")):
                subprocess.run(("git", *args), cwd=repo, check=True, capture_output=True)
            (repo / "a.txt").write_text("base\n", encoding="utf-8")
            subprocess.run(("git", "add", "."), cwd=repo, check=True, capture_output=True)
            subprocess.run(("git", "commit", "-m", "base"), cwd=repo, check=True, capture_output=True)
            sha = subprocess.run(("git", "rev-parse", "HEAD"), cwd=repo, check=True, capture_output=True, text=True).stdout.strip()

            def fake_run(args, **kwargs):
                if args[0] == "git":
                    return subprocess.CompletedProcess(args, 0, "ok", "")
                return subprocess.CompletedProcess(args, returncode, stdout, stderr)

            with mock.patch("ados.review_engine.subprocess.run", side_effect=fake_run):
                return ReviewEngine().run(
                    policy=ExecutionPolicy.from_mapping(
                        {
                            "execution_policy": {
                                "schema_version": "1",
                                "publication": {"merge_strategy": "merge"},
                                "review": {"reviewer": "reviewer-cli", "max_rounds": 5},
                                "cleanup": {"autonomous": True},
                                "guardian": {"stop_on_uncertain": True},
                                "validation": {"commands": ["git diff --check"]},
                            }
                        }
                    ),
                    request=ReviewRequest(repository_path=repo, candidate_sha=sha, base_sha=sha, scope="a.txt"),
                )

    def _category(self, result):
        self.assertEqual("REVIEWER_COMMAND_FAILED", result.violations[0].code)
        return result.violations[0].evidence["runtime_category"]

    def test_oauth_session_expired_is_authentication_unavailable(self):
        result = self._run_reviewer(stderr="OAuth session expired and could not be refreshed")
        self.assertEqual("AUTHENTICATION_UNAVAILABLE", self._category(result))

    def test_reviewer_quota_error(self):
        result = self._run_reviewer(stderr="Error: quota exceeded for this account")
        self.assertIn(self._category(result), {"QUOTA_EXHAUSTED", "USAGE_LIMIT_REACHED"})

    def test_reviewer_capacity_error(self):
        result = self._run_reviewer(stderr="503 service unavailable: server is overloaded")
        self.assertEqual("CAPACITY_UNAVAILABLE", self._category(result))

    def test_reviewer_transient_runtime_error(self):
        result = self._run_reviewer(stderr="connection reset by peer")
        self.assertEqual("TRANSIENT_RUNTIME_UNAVAILABLE", self._category(result))

    def test_unknown_reviewer_command_failure(self):
        result = self._run_reviewer(stderr="AssertionError: reviewer plugin crashed")
        self.assertEqual("UNKNOWN_RUNTIME_FAILURE", self._category(result))

    def test_missing_reviewer_executable_is_command_not_found(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp) / "repo"
            repo.mkdir()
            for args in (("init", "-b", "main"), ("config", "user.email", "t@t.invalid"), ("config", "user.name", "T")):
                subprocess.run(("git", *args), cwd=repo, check=True, capture_output=True)
            (repo / "a.txt").write_text("base\n", encoding="utf-8")
            subprocess.run(("git", "add", "."), cwd=repo, check=True, capture_output=True)
            subprocess.run(("git", "commit", "-m", "base"), cwd=repo, check=True, capture_output=True)
            sha = subprocess.run(("git", "rev-parse", "HEAD"), cwd=repo, check=True, capture_output=True, text=True).stdout.strip()
            policy = ExecutionPolicy.from_mapping(
                {
                    "execution_policy": {
                        "schema_version": "1",
                        "publication": {"merge_strategy": "merge"},
                        "review": {"reviewer": "definitely-not-a-real-binary-xyz --flag", "max_rounds": 5},
                        "cleanup": {"autonomous": True},
                        "guardian": {"stop_on_uncertain": True},
                        "validation": {"commands": ["git diff --check"]},
                    }
                }
            )
            result = ReviewEngine().run(
                policy=policy,
                request=ReviewRequest(repository_path=repo, candidate_sha=sha, base_sha=sha, scope="a.txt"),
            )
        self.assertEqual("REVIEWER_EXECUTABLE_NOT_FOUND", result.violations[0].code)
        self.assertEqual("COMMAND_NOT_FOUND", result.violations[0].evidence["runtime_category"])

    def test_transient_gate_does_not_blindly_trust_reviewer_command_failed(self):
        unknown = ReviewViolation("REVIEWER_COMMAND_FAILED", "x", {"exit_code": "1", "runtime_category": "UNKNOWN_RUNTIME_FAILURE"})
        auth = ReviewViolation("REVIEWER_COMMAND_FAILED", "x", {"exit_code": "1", "runtime_category": "AUTHENTICATION_UNAVAILABLE"})
        transient = ReviewViolation("REVIEWER_COMMAND_FAILED", "x", {"exit_code": "1", "runtime_category": "TRANSIENT_RUNTIME_UNAVAILABLE"})
        self.assertFalse(_is_transient_review_block(_review_block(unknown)))
        self.assertFalse(_is_transient_review_block(_review_block(auth)))
        self.assertTrue(_is_transient_review_block(_review_block(transient)))
        # legacy record without a classified category keeps prior transient-resume semantics
        legacy = ReviewViolation("REVIEWER_COMMAND_FAILED", "x", {"exit_code": "1"})
        self.assertTrue(_is_transient_review_block(_review_block(legacy)))


_REVIEWER_UNKNOWN_FAILURE = (
    "import sys\n"
    "from pathlib import Path\n"
    f"c = Path(r'@COUNTER@')\n"
    "c.write_text(str((int(c.read_text()) if c.exists() else 0) + 1))\n"
    "print('AssertionError: reviewer plugin crashed', file=sys.stderr)\n"
    "sys.exit(1)\n"
)


class ReviewerRuntimeClassificationIntegrationTests(_RoleRunMixin, unittest.TestCase):
    def test_unknown_reviewer_failure_is_durable_and_not_auto_resumed(self):
        with _RoleProject(self, codex_script=IMPL_SUCCESS, claude_script=IMPL_SUCCESS) as fixture:
            counter = fixture.root / "rcount.txt"
            reviewer = fixture.root / "unknown_reviewer.py"
            reviewer.write_text(_REVIEWER_UNKNOWN_FAILURE.replace("@COUNTER@", str(counter)), encoding="utf-8")
            reviewer_cmd = f'"{sys.executable}" "{reviewer}"'
            raw = json.loads(fixture.config.read_text(encoding="utf-8"))
            raw["execution_policy"]["review"]["reviewer"] = reviewer_cmd
            raw["execution_policy"]["agent_roles"]["agents"]["claude"] = reviewer_cmd
            fixture.config.write_text(json.dumps(raw), encoding="utf-8")

            first = RunService().run(RunRequest(fixture.repo, "Adaptive role selection", None, fixture.config))
            record = json.loads(self._durable_path(fixture).read_text(encoding="utf-8"))
            invocations_after_first = int(counter.read_text(encoding="utf-8"))

            second = RunService().run(RunRequest(fixture.repo, "Adaptive role selection", None, fixture.config))
            invocations_after_resume = int(counter.read_text(encoding="utf-8"))

        self.assertEqual("REVIEW_BLOCKED", first.status)
        # durably represented well enough to reason about on resume
        self.assertFalse(record["reviewBlock"]["transient"])
        self.assertEqual("UNKNOWN_RUNTIME_FAILURE", record["reviewBlock"]["runtimeCategory"])
        # an unknown reviewer failure is conservative: not auto-resumed, reviewer
        # not silently re-invoked, and nothing reaches publication
        self.assertEqual(1, invocations_after_first)
        self.assertEqual(invocations_after_first, invocations_after_resume)
        self.assertNotIn(second.status, {"COMPLETE", "READY_FOR_PUBLICATION", "MERGED", "PR_CREATED", "PR_READY"})


# --------------------------------------------------------------------------- #
# Blocking finding 2: reviewer failover never breaks independence              #
# (acceptance also covered by SelectAssignmentTests / FailoverTests above)     #
# --------------------------------------------------------------------------- #


class ReviewerFailoverIndependenceTests(unittest.TestCase):
    def test_candidate_implementer_is_never_its_own_fallback_reviewer(self):
        # codex implements -> claude must review; codex may not be reused
        assignment = select_assignment(policy=_policy(ADAPTIVE_ROLES))
        self.assertEqual("codex", assignment.implementer_id)
        self.assertEqual("claude", assignment.reviewer_id)
        self.assertNotEqual(assignment.implementer_id, assignment.reviewer_id)

    def test_no_independent_reviewer_fails_closed(self):
        roles = {**ADAPTIVE_ROLES, "reviewer_preference": ["codex"]}
        with self.assertRaises(RoleSelectionError) as ctx:
            select_assignment(policy=_policy(roles))
        self.assertEqual("NO_INDEPENDENT_REVIEWER", ctx.exception.code)


if __name__ == "__main__":
    unittest.main()

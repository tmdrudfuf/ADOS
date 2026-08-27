import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

from ados.implementer_runtime import ImplementerCommand, ImplementerRuntime, _bounded
from ados.project_config import load_project_config


class ImplementerRuntimeTests(unittest.TestCase):
    def test_ready_run_invokes_implementer_and_uses_worktree_cwd(self):
        with self.project() as fixture:
            runner = self.runner(fixture.root / "runner.py", "cwd")
            config = self.write_config(fixture.config, fixture.repo, implementer=f'"{sys.executable}" "{runner}"')
            record_path = self.ready_run(fixture, config)
            outcome = ImplementerRuntime().run(config=config, run_record_path=record_path)

            evidence = json.loads(record_path.with_name("implementer-runtime.json").read_text(encoding="utf-8"))

        self.assertEqual("READY_FOR_VALIDATION", outcome.status)
        self.assertEqual(str(fixture.worktree), outcome.result.stdout.strip())
        self.assertEqual(str(fixture.worktree), evidence["runtime"]["command"]["workingDirectory"])

    def test_primary_repo_never_used_as_cwd(self):
        with self.project() as fixture:
            runner = self.runner(fixture.root / "runner.py", "cwd")
            config = self.write_config(fixture.config, fixture.repo, implementer=f'"{sys.executable}" "{runner}"')
            record_path = self.ready_run(fixture, config)
            outcome = ImplementerRuntime().run(config=config, run_record_path=record_path)

        self.assertNotEqual(str(fixture.repo), outcome.result.stdout.strip())

    def test_provider_adapter_resolved_from_config_and_fake_adapter(self):
        with self.project() as fixture:
            runner = self.runner(fixture.root / "fake.py", "success")
            config = self.write_config(fixture.config, fixture.repo, implementer=f'"{sys.executable}" "{runner}"')
            record_path = self.ready_run(fixture, config)
            outcome = ImplementerRuntime().run(config=config, run_record_path=record_path)

        self.assertEqual("READY_FOR_VALIDATION", outcome.status)
        self.assertEqual(sys.executable, outcome.runtime.command.executable)

    def test_codex_adapter_fixture_dry_context(self):
        with self.project() as fixture:
            config = self.write_config(fixture.config, fixture.repo, implementer="codex")
            record_path = self.ready_run(fixture, config)
            record = json.loads(record_path.read_text(encoding="utf-8"))

        self.assertEqual("codex", record["implementer"])

    def test_missing_executable_and_unsafe_command(self):
        with self.project() as missing:
            config = self.write_config(missing.config, missing.repo, implementer="missing-ados-implementer-exe")
            missing_result = ImplementerRuntime().run(config=config, run_record_path=self.ready_run(missing, config))
        with self.project() as unsafe:
            config = self.write_config(unsafe.config, unsafe.repo, implementer=f"{sys.executable} -c pass & echo bad")
            unsafe_result = ImplementerRuntime().run(config=config, run_record_path=self.ready_run(unsafe, config))

        self.assertEqual("BLOCKED", missing_result.status)
        self.assertEqual("IMPLEMENTER_EXECUTABLE_NOT_FOUND", missing_result.violations[0].code)
        self.assertEqual("BLOCKED", unsafe_result.status)
        self.assertEqual("IMPLEMENTER_COMMAND_UNSAFE", unsafe_result.violations[0].code)

    def test_role_mismatch_missing_worktree_wrong_branch_and_stale_base(self):
        with self.project() as role:
            config = self.write_config(role.config, role.repo, implementer="fake-a")
            record_path = self.ready_run(role, config, implementer="fake-b")
            role_result = ImplementerRuntime().run(config=config, run_record_path=record_path)
        with self.project() as missing:
            config = self.write_config(missing.config, missing.repo, implementer="fake-a")
            record_path = self.ready_run(missing, config, external=True)
            missing_result = ImplementerRuntime().run(config=config, run_record_path=record_path)
        with self.project() as branch:
            config = self.write_config(branch.config, branch.repo, implementer="fake-a")
            record_path = self.ready_run(branch, config)
            self.git(branch.worktree, "checkout", "-b", "other-branch")
            branch_result = ImplementerRuntime().run(config=config, run_record_path=record_path)
        with self.project() as stale:
            config = self.write_config(stale.config, stale.repo, implementer="fake-a")
            unrelated = self.git(stale.repo, "commit-tree", "HEAD^{tree}", "-m", "unrelated").stdout.strip()
            record_path = self.ready_run(stale, config, base=unrelated)
            stale_result = ImplementerRuntime().run(config=config, run_record_path=record_path)

        self.assertEqual("IMPLEMENTER_ROLE_MISMATCH", role_result.violations[0].code)
        self.assertEqual("WORKTREE_MISSING", missing_result.violations[0].code)
        self.assertEqual("WORKTREE_BRANCH_MISMATCH", branch_result.violations[0].code)
        self.assertIn("WORKTREE_BASE_STALE", {item.code for item in stale_result.violations})

    def test_success_failure_timeout_stdout_stderr_and_parity(self):
        with self.project() as success:
            runner = self.runner(success.root / "success.py", "stdout_stderr")
            config = self.write_config(success.config, success.repo, implementer=f'"{sys.executable}" "{runner}"')
            ok = ImplementerRuntime().run(config=config, run_record_path=self.ready_run(success, config))
        with self.project() as failure:
            runner = self.runner(failure.root / "failure.py", "failure")
            config = self.write_config(failure.config, failure.repo, implementer=f'"{sys.executable}" "{runner}"')
            failed = ImplementerRuntime().run(config=config, run_record_path=self.ready_run(failure, config))
        with self.project() as timeout:
            runner = self.runner(timeout.root / "timeout.py", "timeout")
            config = self.write_config(timeout.config, timeout.repo, implementer=f'"{sys.executable}" "{runner}"')
            timed = ImplementerRuntime().run(config=config, run_record_path=self.ready_run(timeout, config), timeout_ms=100)

        self.assertEqual("READY_FOR_VALIDATION", ok.status)
        self.assertIn("hello stdout", ok.result.stdout)
        self.assertIn("hello stderr", ok.result.stderr)
        self.assertEqual(ok.status, ok.result.status)
        self.assertEqual("IMPLEMENTATION_FAILED", failed.status)
        self.assertEqual("IMPLEMENTATION_TIMED_OUT", timed.status)

    def test_utf8_stdout_unicode_stderr_and_invalid_bytes_are_captured(self):
        with self.project() as utf8:
            runner = self.runner(utf8.root / "unicode.py", "unicode_output")
            config = self.write_config(utf8.config, utf8.repo, implementer=f'"{sys.executable}" "{runner}"')
            ok = ImplementerRuntime().run(config=config, run_record_path=self.ready_run(utf8, config))
        with self.project() as invalid:
            runner = self.runner(invalid.root / "invalid.py", "invalid_bytes")
            config = self.write_config(invalid.config, invalid.repo, implementer=f'"{sys.executable}" "{runner}"')
            failed = ImplementerRuntime().run(config=config, run_record_path=self.ready_run(invalid, config))

        self.assertEqual("READY_FOR_VALIDATION", ok.status)
        self.assertIn("snowman", ok.result.stdout)
        self.assertIn("\u2603", ok.result.stdout)
        self.assertIn("rocket", ok.result.stderr)
        self.assertIn("\U0001f680", ok.result.stderr)
        self.assertEqual("IMPLEMENTATION_FAILED", failed.status)
        self.assertIn("\ufffd", failed.result.stdout)
        self.assertIn("stderr-ok", failed.result.stderr)
        self.assertEqual(9, failed.result.exit_code)

    def test_bounded_evidence_handles_none_ascii_and_truncation(self):
        self.assertEqual("", _bounded(None))
        self.assertEqual("plain ascii", _bounded("plain ascii"))
        self.assertEqual("abc", _bounded(b"abc"))
        self.assertEqual("\ufffd", _bounded(b"\x9d"))
        self.assertEqual(20_000, len(_bounded("x" * 20_001)))

    def test_subprocess_capture_uses_utf8_replacement_and_no_shell(self):
        with self.project() as fixture:
            command = ImplementerCommand("fake", sys.executable, ("-c", "print('ok')"), str(fixture.worktree), 1000)
            with mock.patch("ados.implementer_runtime.subprocess.run") as mocked:
                mocked.return_value = subprocess.CompletedProcess((sys.executable, "-c", "print('ok')"), 0, "ok", "warn")
                completed = ImplementerRuntime()._spawn(command, "handoff")

        kwargs = mocked.call_args.kwargs
        self.assertEqual(0, completed.returncode)
        self.assertIs(kwargs["shell"], False)
        self.assertEqual("utf-8", kwargs["encoding"])
        self.assertEqual("replace", kwargs["errors"])
        self.assertIs(kwargs["text"], True)

    def test_primary_dirty_after_invocation_blocks(self):
        with self.project() as fixture:
            runner = fixture.root / "dirty_primary.py"
            runner.write_text(
                f"from pathlib import Path\nPath(r'{fixture.repo / 'README.md'}').write_text('dirty', encoding='utf-8')\n",
                encoding="utf-8",
            )
            config = self.write_config(fixture.config, fixture.repo, implementer=f'"{sys.executable}" "{runner}"')
            outcome = ImplementerRuntime().run(config=config, run_record_path=self.ready_run(fixture, config))

        self.assertEqual("BLOCKED", outcome.status)
        self.assertIn("PRIMARY_DIRTY_TRACKED_FILES", {item.code for item in outcome.violations})

    def test_no_validation_review_publication_and_repeated_completed_run_is_idempotent(self):
        with self.project() as fixture:
            runner = self.runner(fixture.root / "runner.py", "success")
            config = self.write_config(fixture.config, fixture.repo, implementer=f'"{sys.executable}" "{runner}"')
            record_path = self.ready_run(fixture, config)
            first = ImplementerRuntime().run(config=config, run_record_path=record_path)
            second = ImplementerRuntime().run(config=config, run_record_path=record_path)

        self.assertEqual("READY_FOR_VALIDATION", first.status)
        self.assertEqual("READY_FOR_VALIDATION", second.status)
        self.assertIsNone(second.result)

    def test_validation_recovery_handoff_surfaces_stderr_and_allows_focused_diagnostics(self):
        with self.project() as fixture:
            runner = self.runner(fixture.root / "runner.py", "success")
            config = self.write_config(fixture.config, fixture.repo, implementer=f'"{sys.executable}" "{runner}"')
            record_path = self.ready_run(fixture, config)
            record = json.loads(record_path.read_text(encoding="utf-8"))
            record["status"] = "VALIDATION_FAILED"
            record["nextStage"] = "implementation_recovery"
            record["validationFailure"] = {
                "recoveryStage": "implementation_recovery",
                "candidateSha": "abc123",
                "status": "BLOCK",
                "headBefore": "abc123",
                "headAfter": "abc123",
                "failedCommands": [
                    {
                        "command": "npm test",
                        "exitCode": 1,
                        "reasonCode": "VALIDATION_COMMAND_FAILED",
                        "stdout": "many passing test rows before the failure",
                        "stderr": "AssertionError: expected visible row",
                    }
                ],
            }
            record_path.write_text(json.dumps(record, indent=2), encoding="utf-8")
            outcome = ImplementerRuntime().run(config=config, run_record_path=record_path)

        handoff = outcome.runtime.handoff
        self.assertEqual("READY_FOR_VALIDATION", outcome.status)
        self.assertIn("Implementation recovery context:", handoff)
        self.assertIn("Read the validation artifact and identify the root cause before editing.", handoff)
        self.assertIn("You may run focused diagnostic commands or tests for the failing area.", handoff)
        self.assertIn("leave the full configured validation pipeline to ADOS", handoff)
        self.assertIn("Do not start review, publish, merge, deploy, or mutate GitHub", handoff)
        self.assertNotIn("Do not run validation, start review", handoff)
        self.assertLess(handoff.index("stderr: AssertionError: expected visible row"), handoff.index("stdout: many passing test rows"))

    def test_durable_state_serialization_resume_compatibility_and_handoff_bound(self):
        with self.project(project_id="aiverse-shaped") as fixture:
            runner = self.runner(fixture.root / "runner.py", "success")
            config = self.write_config(fixture.config, fixture.repo, project_id="aiverse-shaped", implementer=f'"{sys.executable}" "{runner}"')
            record_path = self.ready_run(fixture, config)
            outcome = ImplementerRuntime().run(config=config, run_record_path=record_path)
            record = json.loads(record_path.read_text(encoding="utf-8"))
            evidence = json.loads(record_path.with_name("implementer-runtime.json").read_text(encoding="utf-8"))

        self.assertEqual("READY_FOR_VALIDATION", record["status"])
        self.assertEqual("validation", record["nextStage"])
        self.assertLess(len(outcome.runtime.handoff), 3000)
        self.assertEqual("aiverse-shaped", evidence["runRecord"]["projectId"] if "runRecord" in evidence else record["projectId"])

    def project(self, **kwargs):
        return TemporaryProject(self, **kwargs)

    def init_repo(self, repo):
        repo.mkdir(parents=True)
        self.git(repo, "init", "-b", "main")
        self.git(repo, "config", "user.email", "test@example.invalid")
        self.git(repo, "config", "user.name", "Test User")
        (repo / ".gitignore").write_text(".agent-workflow/\n", encoding="utf-8")
        (repo / "README.md").write_text("base\n", encoding="utf-8")
        self.git(repo, "add", ".gitignore", "README.md")
        self.git(repo, "commit", "-m", "initial")
        self.git(repo, "remote", "add", "origin", str(repo))
        self.git(repo, "update-ref", "refs/remotes/origin/main", self.head(repo))

    def write_config(self, path, repo, *, project_id="example-project", implementer):
        raw = {
            "project": {
                "id": project_id,
                "primary_repository_path": str(repo),
                "default_branch": "main",
                "allowed_primary_local_paths": [],
            },
            "roles": {"implementer": implementer, "reviewer": "claude"},
            "execution_policy": {
                "schema_version": "1",
                "publication": {"merge_strategy": "merge"},
                "review": {"reviewer": "claude", "max_rounds": 5},
                "cleanup": {"autonomous": True},
                "guardian": {"stop_on_uncertain": True},
                "validation": {"commands": ["git diff --check"]},
            },
        }
        path.write_text(json.dumps(raw), encoding="utf-8")
        return load_project_config(path)

    def ready_run(self, fixture, config, *, implementer=None, base=None, external=False):
        if not fixture.worktree.exists() and not external:
            self.git(fixture.repo, "worktree", "add", "-b", fixture.branch, str(fixture.worktree), self.head(fixture.repo))
        base = base or self.head(fixture.repo)
        record = {
            "runId": "run-123",
            "projectId": config.project_id,
            "specNumber": "014",
            "featureSlug": "fixture",
            "featureDescription": "Fixture implementation",
            "authoritativeBaseSha": base,
            "primaryRepository": str(fixture.repo),
            "featureBranch": fixture.branch,
            "featureWorktree": str(fixture.worktree),
            "implementer": implementer or config.implementer,
            "reviewer": config.reviewer,
            "executionPolicyVersion": config.execution_policy.schema_version,
            "status": "READY_FOR_IMPLEMENTATION",
            "nextStage": "implementation_handoff",
        }
        root = fixture.root / "external-run" if external else fixture.worktree
        path = root / ".agent-workflow" / "runs" / "014-fixture" / "ados-run.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(record, indent=2), encoding="utf-8")
        return path

    def runner(self, path, mode):
        scripts = {
            "cwd": "import os\nprint(os.getcwd())\n",
            "success": "from pathlib import Path\nPath('implementation.txt').write_text('ok', encoding='utf-8')\nprint('done')\n",
            "stdout_stderr": "import sys\nprint('hello stdout')\nprint('hello stderr', file=sys.stderr)\n",
            "unicode_output": "import sys\nsys.stdout.buffer.write('snowman \u2603\\n'.encode('utf-8'))\nsys.stderr.buffer.write('rocket \U0001f680\\n'.encode('utf-8'))\n",
            "invalid_bytes": "import sys\nsys.stdout.buffer.write(b'bad-\\x9d-byte')\nsys.stderr.write('stderr-ok')\nsys.exit(9)\n",
            "failure": "import sys\nprint('bad', file=sys.stderr)\nsys.exit(7)\n",
            "timeout": "import time\ntime.sleep(5)\n",
        }
        path.write_text(scripts[mode], encoding="utf-8")
        return path

    def git(self, repo, *args):
        return subprocess.run(("git", *args), cwd=repo, check=True, capture_output=True, text=True)

    def head(self, repo):
        return self.git(repo, "rev-parse", "HEAD").stdout.strip()


class TemporaryProject:
    def __init__(self, test, **kwargs):
        self.test = test
        self.kwargs = kwargs

    def __enter__(self):
        self.temp = tempfile.TemporaryDirectory(prefix="ados implementer path ")
        self.root = Path(self.temp.name)
        self.repo = self.root / "project repo"
        self.worktree = self.root / "feature worktree"
        self.branch = "codex/014-fixture"
        self.config = self.root / "project-config.json"
        self.test.init_repo(self.repo)
        self.project_id = self.kwargs.get("project_id", "example-project")
        return self

    def __exit__(self, exc_type, exc, traceback):
        self.temp.cleanup()

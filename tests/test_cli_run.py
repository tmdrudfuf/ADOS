import contextlib
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from ados.cli import main
from ados.run_command import RunRequest, RunService
from ados.status import StatusRequest, StatusService


class CliRunTests(unittest.TestCase):
    def test_help_argument_parsing(self):
        completed = subprocess.run((sys.executable, "-m", "ados", "run", "--help"), cwd=Path.cwd(), capture_output=True, text=True)

        self.assertEqual(0, completed.returncode)
        self.assertIn("--feature", completed.stdout)

    def test_valid_run_start(self):
        with self.project(specs=[1, 2]) as fixture:
            code, stdout = self.run_cli("run", "--project", str(fixture.repo), "--config", str(fixture.config), "--feature", "Add run command")
            payload = self.read_run_record(fixture.root / "project-add-run-command", "003-add-run-command")

        self.assertEqual(0, code)
        self.assertIn("READY_FOR_VALIDATION", stdout)
        self.assertEqual("003", payload["specNumber"])
        self.assertEqual("READY_FOR_VALIDATION", payload["status"])
        self.assertEqual("codex/003-add-run-command", payload["featureBranch"])

    def test_explicit_spec(self):
        with self.project(specs=[1]) as fixture:
            result = RunService().run(RunRequest(fixture.repo, "Build status handoff", 7, fixture.config))

        self.assertEqual("READY_FOR_VALIDATION", result.status)
        self.assertEqual("007", result.plan.spec_number)

    def test_automatic_next_spec(self):
        with self.project(specs=[1, 3]) as fixture:
            result = RunService().run(RunRequest(fixture.repo, "Fill gap", None, fixture.config, dry_run=True))

        self.assertEqual("PLANNED", result.status)
        self.assertEqual("002", result.plan.spec_number)

    def test_used_spec_blocks(self):
        with self.project(specs=[4]) as fixture:
            result = RunService().run(RunRequest(fixture.repo, "Duplicate", 4, fixture.config, dry_run=True))

        self.assertEqual("BLOCKED", result.status)
        self.assertIn("SPEC_NUMBER_USED", self.codes(result))

    def test_ambiguous_conflicting_worktree_blocks(self):
        with self.project() as fixture:
            worktree = fixture.root / "conflict"
            self.git(fixture.repo, "worktree", "add", "-b", "codex/005-existing", str(worktree), "HEAD")
            result = RunService().run(RunRequest(fixture.repo, "New thing", 5, fixture.config, dry_run=True))
            self.git(fixture.repo, "worktree", "remove", str(worktree))

        self.assertEqual("BLOCKED", result.status)
        self.assertIn("CONFLICTING_WORKTREE", self.codes(result))

    def test_dirty_primary_blocks_before_mutation(self):
        with self.project() as fixture:
            (fixture.repo / "README.md").write_text("dirty\n", encoding="utf-8")
            before = self.snapshot(fixture.repo)
            result = RunService().run(RunRequest(fixture.repo, "Dirty start", None, fixture.config))
            after = self.snapshot(fixture.repo)

        self.assertEqual("BLOCKED", result.status)
        self.assertIn("DIRTY_TRACKED_FILES", self.codes(result))
        self.assertEqual(before, after)

    def test_invalid_config_blocks(self):
        with self.project() as fixture:
            fixture.config.write_text("{ bad json", encoding="utf-8")
            result = RunService().run(RunRequest(fixture.repo, "Invalid", None, fixture.config, dry_run=True))

        self.assertEqual("INVALID", result.status)
        self.assertIn("PROJECT_CONFIG_INVALID_JSON", self.codes(result))

    def test_out_of_sync_base_blocks(self):
        with self.project() as fixture:
            self.git(fixture.repo, "commit", "--allow-empty", "-m", "local only")
            result = RunService().run(RunRequest(fixture.repo, "Out of sync", None, fixture.config, dry_run=True))

        self.assertEqual("BLOCKED", result.status)
        self.assertIn("DEFAULT_BRANCH_NOT_SYNCED", self.codes(result))

    def test_unrelated_safe_worktree_does_not_block(self):
        with self.project() as fixture:
            worktree = fixture.root / "other"
            self.git(fixture.repo, "worktree", "add", "-b", "codex/099-other", str(worktree), "HEAD")
            result = RunService().run(RunRequest(fixture.repo, "Fresh start", 5, fixture.config, dry_run=True))
            self.git(fixture.repo, "worktree", "remove", str(worktree))

        self.assertEqual("BLOCKED", result.status)
        self.assertIn("ACTIVE_WORKTREE_PRESENT", self.codes(result))

    def test_status_human_intervention_blocks_before_mutation(self):
        with self.project() as fixture:
            first = fixture.root / "first"
            second = fixture.root / "second"
            self.git(fixture.repo, "worktree", "add", "-b", "codex/099-first", str(first), "HEAD")
            self.git(fixture.repo, "worktree", "add", "-b", "codex/098-second", str(second), "HEAD")
            before = self.snapshot(fixture.repo)
            result = RunService().run(RunRequest(fixture.repo, "Must not start", 5, fixture.config))
            after = self.snapshot(fixture.repo)
            self.git(fixture.repo, "worktree", "remove", str(first))
            self.git(fixture.repo, "worktree", "remove", str(second))

        self.assertEqual("BLOCKED", result.status)
        self.assertIn("UNSAFE_RECOVERY_STATE", self.codes(result))
        self.assertEqual(before, after)
        self.assertEqual(before, after)

    def test_multiple_historical_merged_worktrees_do_not_block_new_run(self):
        with self.project(specs=[1, 2, 3]) as fixture:
            self.write_archive(fixture.repo, spec="001-example", merge_commit=self.head(fixture.repo))
            self.write_archive(fixture.repo, spec="002-example", merge_commit=self.head(fixture.repo))
            self.write_archive(fixture.repo, spec="003-example", merge_commit=self.head(fixture.repo))
            first = fixture.root / "old-001"
            second = fixture.root / "old-002"
            self.git(fixture.repo, "worktree", "add", "-b", "codex/001-old", str(first), "HEAD")
            self.git(fixture.repo, "worktree", "add", "-b", "codex/002-old", str(second), "HEAD")
            before = self.snapshot(fixture.repo)
            result = RunService().run(RunRequest(fixture.repo, "New production fix", 4, fixture.config, dry_run=True))
            after = self.snapshot(fixture.repo)
            self.git(fixture.repo, "worktree", "remove", str(first))
            self.git(fixture.repo, "worktree", "remove", str(second))

        self.assertEqual("PLANNED", result.status)
        self.assertEqual(before, after)
        self.assertTrue(any(warning.code == "HISTORICAL_WORKTREES_PRESENT" for warning in result.eligibility.warnings))

    def test_unknown_dirty_historical_worktree_blocks_run_start(self):
        with self.project(specs=[1]) as fixture:
            self.write_archive(fixture.repo, spec="001-example", merge_commit=self.head(fixture.repo))
            worktree = fixture.root / "dirty-historical"
            self.git(fixture.repo, "worktree", "add", "-b", "codex/001-old", str(worktree), "HEAD")
            (worktree / "scratch.txt").write_text("unique\n", encoding="utf-8")
            before = self.snapshot(fixture.repo)
            result = RunService().run(RunRequest(fixture.repo, "Blocked by dirty old tree", 2, fixture.config, dry_run=True))
            after = self.snapshot(fixture.repo)
            (worktree / "scratch.txt").unlink()
            self.git(fixture.repo, "worktree", "remove", str(worktree))

        self.assertEqual("BLOCKED", result.status)
        self.assertIn("UNSAFE_RECOVERY_STATE", self.codes(result))

    def test_abandoned_lower_numbered_spec_branch_blocks_run_start(self):
        with self.project(specs=[1, 2, 3]) as fixture:
            self.write_archive(fixture.repo, spec="003-example", merge_commit=self.head(fixture.repo))
            worktree = fixture.root / "abandoned-002"
            self.git(fixture.repo, "worktree", "add", "-b", "codex/002-abandoned", str(worktree), "HEAD")
            (worktree / "abandoned.txt").write_text("abandoned\n", encoding="utf-8")
            self.git(worktree, "add", "abandoned.txt")
            self.git(worktree, "commit", "-m", "abandoned spec work")
            before = self.snapshot(fixture.repo)
            result = RunService().run(RunRequest(fixture.repo, "New work", 4, fixture.config, dry_run=True))
            after = self.snapshot(fixture.repo)
            self.git(fixture.repo, "worktree", "remove", str(worktree))

        self.assertEqual("BLOCKED", result.status)
        self.assertIn("ACTIVE_WORKTREE_PRESENT", self.codes(result))
        self.assertEqual(before, after)

    def test_preserved_worktree_blocks_run_start(self):
        with self.project() as fixture:
            worktree = fixture.root / "preserved"
            self.git(fixture.repo, "worktree", "add", "-b", "experiment-preserve", str(worktree), "HEAD")
            (worktree / "preserved.txt").write_text("preserve\n", encoding="utf-8")
            self.git(worktree, "add", "preserved.txt")
            self.git(worktree, "commit", "-m", "preserved branch work")
            result = RunService().run(RunRequest(fixture.repo, "Blocked by preserved", 2, fixture.config, dry_run=True))
            self.git(fixture.repo, "worktree", "remove", str(worktree))

        self.assertEqual("BLOCKED", result.status)
        self.assertIn("UNSAFE_RECOVERY_STATE", self.codes(result))

    def test_stale_historical_evidence_warns_not_blocks(self):
        with self.project() as fixture:
            self.write_archive(fixture.repo, validated_sha="1" * 40, approved_review_sha="1" * 40)
            result = RunService().run(RunRequest(fixture.repo, "Ignore old evidence", None, fixture.config, dry_run=True))

        self.assertEqual("PLANNED", result.status)
        self.assertTrue(result.eligibility.warnings)

    def test_changes_requested_review_state_does_not_claim_recovery_block(self):
        with self.project() as fixture:
            self.write_archive(fixture.repo, approved_review_sha=self.head(fixture.repo), decision="Changes Requested")
            result = RunService().run(RunRequest(fixture.repo, "New unrelated spec", None, fixture.config, dry_run=True))

        self.assertEqual("PLANNED", result.status)
        self.assertNotIn("UNSAFE_RECOVERY_STATE", self.codes(result))

    def test_worktree_created_before_any_run_record_write(self):
        with self.project() as fixture:
            result = RunService().run(RunRequest(fixture.repo, "Ordering proof", None, fixture.config))
            record = next(Path(result.run_record.feature_worktree, ".agent-workflow", "runs").glob("*/ados-run.json"))
            self.assertEqual("READY_FOR_VALIDATION", result.status)
            self.assertTrue(record.is_file())
            self.assertTrue(Path(result.run_record.feature_worktree, ".git").exists())

    def test_branch_derived_windows_spaces_and_deterministic_identity(self):
        with tempfile.TemporaryDirectory(prefix="ados run path ") as directory:
            root = Path(directory)
            repo = root / "project repo"
            self.init_repo(repo)
            config = self.write_config(root / "config.json", repo)
            first = RunService().run(RunRequest(repo, "Windows Path Feature", 8, config, dry_run=True))
            second = RunService().run(RunRequest(repo, "Windows Path Feature", 8, config, dry_run=True))

        self.assertEqual("codex/008-windows-path-feature", first.plan.feature_branch)
        self.assertEqual(first.run_record.run_id, second.run_record.run_id)

    def test_run_record_serialization_and_status_sees_active_run(self):
        with self.project() as fixture:
            result = RunService().run(RunRequest(fixture.repo, "Status sees run", None, fixture.config))
            status = StatusService().run(StatusRequest(fixture.repo, fixture.config))

        self.assertEqual("READY_FOR_VALIDATION", result.run_record.status)
        self.assertEqual(result.run_record.run_id, status.workflow.evidence["run_id"])

    def test_dry_run_has_zero_mutations(self):
        with self.project() as fixture:
            before = self.snapshot(fixture.repo)
            result = RunService().run(RunRequest(fixture.repo, "Plan only", None, fixture.config, dry_run=True))
            after = self.snapshot(fixture.repo)

        self.assertEqual("PLANNED", result.status)
        self.assertEqual(before, after)

    def test_partial_worktree_failure_is_truthful(self):
        with self.project() as fixture:
            planned = fixture.root / "project-failing-create"
            planned.mkdir()
            result = RunService().run(RunRequest(fixture.repo, "Failing create", None, fixture.config))

        self.assertEqual("BLOCKED", result.status)
        self.assertIn("WORKTREE_PATH_EXISTS", self.codes(result))

    def test_no_validation_review_or_publication_side_effects(self):
        with self.project() as fixture:
            result = RunService().run(RunRequest(fixture.repo, "No later stages", None, fixture.config))
            output = self.git(fixture.repo, "log", "--oneline").stdout

        self.assertEqual("READY_FOR_VALIDATION", result.status)
        self.assertNotIn("validation", output.lower())

    def test_repeated_invocation_detects_existing_run(self):
        with self.project() as fixture:
            first = RunService().run(RunRequest(fixture.repo, "Repeatable", None, fixture.config))
            second = RunService().run(RunRequest(fixture.repo, "Repeatable", None, fixture.config))

        self.assertEqual("READY_FOR_VALIDATION", first.status)
        self.assertEqual("BLOCKED", second.status)
        self.assertIn("WORKTREE_PATH_EXISTS", self.codes(second))

    def test_project_neutral_and_ados_self_hosting_shape(self):
        with self.project(project_id="other") as fixture:
            result = RunService().run(RunRequest(fixture.repo, "Self hosting shape", None, fixture.config, dry_run=True))

        self.assertEqual("other", result.run_record.project_id)
        self.assertEqual("PLANNED", result.status)

    def test_json_output(self):
        with self.project() as fixture:
            code, stdout = self.run_cli("run", "--project", str(fixture.repo), "--config", str(fixture.config), "--feature", "Json run", "--dry-run", "--json")

        self.assertEqual(0, code)
        self.assertEqual("PLANNED", json.loads(stdout)["status"])

    def test_cli_implementer_failure_exit_one(self):
        with self.project(implementer_mode="failure") as fixture:
            code, stdout = self.run_cli("run", "--project", str(fixture.repo), "--config", str(fixture.config), "--feature", "Failing implementation", "--json")

        self.assertEqual(1, code)
        self.assertEqual("IMPLEMENTATION_FAILED", json.loads(stdout)["status"])

    def test_cli_implementer_timeout_exit_one(self):
        with self.project(implementer_mode="timeout") as fixture:
            code, stdout = self.run_cli(
                "run",
                "--project",
                str(fixture.repo),
                "--config",
                str(fixture.config),
                "--feature",
                "Timed implementation",
                "--implementer-timeout-ms",
                "100",
                "--json",
            )

        self.assertEqual(1, code)
        self.assertEqual("IMPLEMENTATION_TIMED_OUT", json.loads(stdout)["status"])

    def run_cli(self, *args):
        stream = io.StringIO()
        with contextlib.redirect_stdout(stream):
            code = main(list(args))
        return code, stream.getvalue()

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

    def write_config(self, path, repo, *, project_id="example-project", allowed_paths=(), implementer=None, implementer_mode="success"):
        if implementer is None:
            runner = path.parent / "implementer.py"
            scripts = {
                "success": (
                    "from pathlib import Path\n"
                    "import os, sys\n"
                    "Path('implementation.txt').write_text('implemented', encoding='utf-8')\n"
                    "print(os.getcwd())\n"
                    "print(sys.stdin.read()[:200])\n"
                ),
                "failure": "import sys\nprint('failed', file=sys.stderr)\nsys.exit(9)\n",
                "timeout": "import time\ntime.sleep(5)\n",
            }
            runner.write_text(scripts[implementer_mode], encoding="utf-8")
            implementer = f'"{sys.executable}" "{runner}"'
        config = {
            "project": {
                "id": project_id,
                "primary_repository_path": str(repo),
                "default_branch": "main",
                "allowed_primary_local_paths": list(allowed_paths),
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
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(config), encoding="utf-8")
        return path

    def write_archive(self, repo, *, spec="000-old", validated_sha="", approved_review_sha="", decision="Approved", merge_commit=""):
        archive = repo / ".agent-workflow" / "runs" / spec / "ados-review-evidence.json"
        archive.parent.mkdir(parents=True, exist_ok=True)
        archive.write_text(
            json.dumps(
                {
                    "spec": spec,
                    "validated_sha": validated_sha,
                    "approved_review_sha": approved_review_sha,
                    "claude_decision": decision,
                    "merge_commit": merge_commit,
                }
            ),
            encoding="utf-8",
        )

    def read_run_record(self, worktree, run_dir):
        return json.loads((worktree / ".agent-workflow" / "runs" / run_dir / "ados-run.json").read_text(encoding="utf-8"))

    def codes(self, result):
        return {violation.code for violation in result.eligibility.violations}

    def git(self, repo, *args):
        return subprocess.run(("git", *args), cwd=repo, check=True, capture_output=True, text=True)

    def head(self, repo):
        return self.git(repo, "rev-parse", "HEAD").stdout.strip()

    def snapshot(self, repo):
        return {
            "head": self.head(repo),
            "status": self.git(repo, "status", "--short").stdout,
            "worktrees": self.git(repo, "worktree", "list", "--porcelain").stdout,
        }


class TemporaryProject:
    def __init__(self, test, **kwargs):
        self.test = test
        self.kwargs = kwargs

    def __enter__(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.repo = self.root / "project"
        specs = self.kwargs.pop("specs", ())
        self.test.init_repo(self.repo)
        for number in specs:
            spec_dir = self.repo / "specs" / f"{number:03d}-example"
            spec_dir.mkdir(parents=True, exist_ok=True)
            (spec_dir / "spec.md").write_text("# Spec\n", encoding="utf-8")
        if specs:
            self.test.git(self.repo, "add", "specs")
            self.test.git(self.repo, "commit", "-m", "add specs")
            self.test.git(self.repo, "update-ref", "refs/remotes/origin/main", self.test.head(self.repo))
        self.config = self.test.write_config(self.root / "project-config.json", self.repo, **self.kwargs)
        return self

    def __exit__(self, exc_type, exc, traceback):
        self.temp.cleanup()

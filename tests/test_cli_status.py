import contextlib
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from ados.cli import main
from ados.status import StatusRequest, StatusService


class CliStatusTests(unittest.TestCase):
    def test_root_status_command_parsing(self):
        completed = self.run_module("status", "--help")

        self.assertEqual(0, completed.returncode)
        self.assertIn("--project", completed.stdout)

    def test_valid_project_idle_state_and_exit_zero(self):
        with self.project(specs=[1, 2]) as fixture:
            code, stdout = self.run_cli("status", "--project", str(fixture.repo), "--config", str(fixture.config))

        self.assertEqual(0, code)
        self.assertIn("ADOS Status", stdout)
        self.assertIn("IDLE", stdout)

    def test_blocked_primary_exit_one(self):
        with self.project() as fixture:
            (fixture.repo / "README.md").write_text("dirty\n", encoding="utf-8")
            code, stdout = self.run_cli("status", str(fixture.repo), "--config", str(fixture.config))

        self.assertEqual(1, code)
        self.assertIn("BLOCKED", stdout)
        self.assertIn("HUMAN_INTERVENTION_REQUIRED", stdout)

    def test_invalid_config_exits_two(self):
        with self.project() as fixture:
            fixture.config.write_text("{ bad json", encoding="utf-8")
            code, stdout = self.run_cli("status", "--project", str(fixture.repo), "--config", str(fixture.config), "--json")

        self.assertEqual(2, code)
        self.assertEqual("INVALID", json.loads(stdout)["status"])

    def test_non_git_project_is_invalid_or_blocked(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            project = root / "not-git"
            project.mkdir()
            config = self.write_config(root / "config.json", project)
            code, stdout = self.run_cli("status", "--project", str(project), "--config", str(config), "--json")

        self.assertEqual(1, code)
        self.assertIn("NOT_GIT_REPOSITORY", stdout)

    def test_allowed_local_paths(self):
        with self.project(allowed_paths=[".claude"]) as fixture:
            (fixture.repo / ".claude").mkdir()
            (fixture.repo / ".claude" / "settings.json").write_text("{}", encoding="utf-8")
            result = StatusService().run(StatusRequest(fixture.repo, fixture.config))

        self.assertEqual("SAFE", result.guardian.state)

    def test_active_worktree_detection_and_active_spec(self):
        with self.project(specs=[12]) as fixture:
            worktree = fixture.root / "feature"
            self.git(fixture.repo, "worktree", "add", "-b", "codex/012-status-foundation", str(worktree), "HEAD")
            result = StatusService().run(StatusRequest(fixture.repo, fixture.config))
            self.git(fixture.repo, "worktree", "remove", str(worktree))

        self.assertEqual("ACTIVE", result.status)
        self.assertEqual("012", result.spec.evidence["active_spec"])
        self.assertEqual(2, len(result.worktrees))

    def test_multiple_worktree_reporting_blocks_as_ambiguous(self):
        with self.project() as fixture:
            first = fixture.root / "feature-012"
            second = fixture.root / "feature-013"
            self.git(fixture.repo, "worktree", "add", "-b", "codex/012-status-foundation", str(first), "HEAD")
            self.git(fixture.repo, "worktree", "add", "-b", "codex/013-run-foundation", str(second), "HEAD")
            result = StatusService().run(StatusRequest(fixture.repo, fixture.config))
            self.git(fixture.repo, "worktree", "remove", str(first))
            self.git(fixture.repo, "worktree", "remove", str(second))

        self.assertEqual("BLOCKED", result.status)
        self.assertEqual("2", result.workflow.evidence["active_worktree_count"])

    def test_latest_active_and_next_spec_resolution(self):
        with self.project(specs=[1, 2, 4]) as fixture:
            self.write_archive(fixture.repo, spec="002-cli-foundation", merge_commit=self.head(fixture.repo))
            result = StatusService().run(StatusRequest(fixture.repo, fixture.config))

        self.assertEqual("002", result.spec.evidence["latest_merged_spec"])
        self.assertEqual("None", result.spec.evidence["active_spec"])
        self.assertEqual("003", result.spec.evidence["next_unused_spec"])

    def test_validation_evidence_current_stale_and_unavailable(self):
        with self.project() as current:
            self.write_archive(current.repo, validated_sha=self.head(current.repo))
            current_result = StatusService().run(StatusRequest(current.repo, current.config))
        with self.project() as stale:
            self.write_archive(stale.repo, validated_sha="0" * 40)
            stale_result = StatusService().run(StatusRequest(stale.repo, stale.config))
        with self.project() as unavailable:
            unavailable_result = StatusService().run(StatusRequest(unavailable.repo, unavailable.config))

        self.assertEqual("Passed", current_result.validation.state)
        self.assertEqual("Stale", stale_result.validation.state)
        self.assertEqual("Unavailable", unavailable_result.validation.state)

    def test_review_evidence_approved_changes_requested_stale_and_unavailable(self):
        with self.project() as approved:
            self.write_archive(approved.repo, approved_review_sha=self.head(approved.repo), decision="Approved")
            approved_result = StatusService().run(StatusRequest(approved.repo, approved.config))
        with self.project() as changes:
            self.write_archive(changes.repo, approved_review_sha=self.head(changes.repo), decision="Changes Requested")
            changes_result = StatusService().run(StatusRequest(changes.repo, changes.config))
        with self.project() as stale:
            self.write_archive(stale.repo, approved_review_sha="1" * 40, decision="Approved")
            stale_result = StatusService().run(StatusRequest(stale.repo, stale.config))
        with self.project() as unavailable:
            unavailable_result = StatusService().run(StatusRequest(unavailable.repo, unavailable.config))

        self.assertEqual("Approved", approved_result.review.state)
        self.assertEqual("ChangesRequested", changes_result.review.state)
        self.assertEqual("Stale", stale_result.review.state)
        self.assertEqual("Unavailable", unavailable_result.review.state)

    def test_exact_head_match_mismatch_unavailable(self):
        with self.project() as match:
            head = self.head(match.repo)
            self.write_archive(match.repo, approved_review_sha=head, validated_sha=head)
            match_result = StatusService().run(StatusRequest(match.repo, match.config))
        with self.project() as mismatch:
            self.write_archive(mismatch.repo, approved_review_sha="1" * 40, validated_sha="2" * 40)
            mismatch_result = StatusService().run(StatusRequest(mismatch.repo, mismatch.config))
        with self.project() as unavailable:
            unavailable_result = StatusService().run(StatusRequest(unavailable.repo, unavailable.config))

        self.assertEqual("MATCH", match_result.exact_head_gate.state)
        self.assertEqual("MISMATCH", mismatch_result.exact_head_gate.state)
        self.assertEqual("Unavailable", unavailable_result.exact_head_gate.state)

    def test_publication_evidence(self):
        with self.project() as fixture:
            head = self.head(fixture.repo)
            self.write_archive(fixture.repo, merge_commit=head, pull_request=21)
            result = StatusService().run(StatusRequest(fixture.repo, fixture.config))

        self.assertEqual("Merged", result.publication.state)
        self.assertEqual("21", result.publication.evidence["pull_request"])

    def test_merged_archive_exact_head_gate_is_historical_not_live_mismatch(self):
        with self.project() as fixture:
            head = self.head(fixture.repo)
            self.write_archive(fixture.repo, approved_review_sha="1" * 40, validated_sha="1" * 40, merge_commit=head)
            result = StatusService().run(StatusRequest(fixture.repo, fixture.config))

        self.assertEqual("Merged", result.publication.state)
        self.assertEqual("Unavailable", result.exact_head_gate.state)
        self.assertNotIn("SHA_MISMATCH", result.recovery.reason_codes)

    def test_recovery_and_safe_next_action(self):
        with self.project(specs=[1, 2]) as fixture:
            ready = StatusService().run(StatusRequest(fixture.repo, fixture.config))
            (fixture.repo / "README.md").write_text("dirty\n", encoding="utf-8")
            blocked = StatusService().run(StatusRequest(fixture.repo, fixture.config))

        self.assertEqual("Start Spec 003", ready.next_action.action)
        self.assertEqual("HUMAN_INTERVENTION_REQUIRED", blocked.next_action.action)

    def test_ambiguous_next_action_for_multiple_worktrees(self):
        with self.project() as fixture:
            first = fixture.root / "feature-012"
            second = fixture.root / "feature-013"
            self.git(fixture.repo, "worktree", "add", "-b", "codex/012-status-foundation", str(first), "HEAD")
            self.git(fixture.repo, "worktree", "add", "-b", "codex/013-run-foundation", str(second), "HEAD")
            result = StatusService().run(StatusRequest(fixture.repo, fixture.config))
            self.git(fixture.repo, "worktree", "remove", str(first))
            self.git(fixture.repo, "worktree", "remove", str(second))

        self.assertEqual("HUMAN_INTERVENTION_REQUIRED", result.next_action.action)

    def test_json_output_and_exit_codes(self):
        with self.project() as fixture:
            code, stdout = self.run_cli("status", "--project", str(fixture.repo), "--config", str(fixture.config), "--json")

        self.assertEqual(0, code)
        payload = json.loads(stdout)
        self.assertEqual("IDLE", payload["status"])
        self.assertIn("nextAction", payload)

    def test_windows_paths(self):
        with tempfile.TemporaryDirectory(prefix="ados status path ") as directory:
            root = Path(directory)
            repo = root / "project repo"
            self.init_repo(repo)
            config = self.write_config(root / "config.json", repo)
            result = StatusService().run(StatusRequest(repo, config))

        self.assertEqual("IDLE", result.status)

    def test_no_project_mutation_and_repeated_side_effect_free(self):
        with self.project() as fixture:
            before = self.snapshot(fixture.repo)
            first = StatusService().run(StatusRequest(fixture.repo, fixture.config))
            second = StatusService().run(StatusRequest(fixture.repo, fixture.config))
            after = self.snapshot(fixture.repo)

        self.assertEqual("IDLE", first.status)
        self.assertEqual("IDLE", second.status)
        self.assertEqual(before, after)

    def test_aiverse_integration_read_only_shape(self):
        with self.project(project_id="aiverse", allowed_paths=[".claude"], specs=range(1, 84)) as fixture:
            result = StatusService().run(StatusRequest(fixture.repo, fixture.config))

        self.assertEqual("aiverse", result.project.evidence["project_id"])
        self.assertEqual("084", result.spec.evidence["next_unused_spec"])

    def test_project_neutral_behavior(self):
        with self.project(project_id="other-project") as fixture:
            result = StatusService().run(StatusRequest(fixture.repo, fixture.config))

        self.assertEqual("other-project", result.project.evidence["project_id"])

    def run_module(self, *args):
        return subprocess.run((sys.executable, "-m", "ados", *args), cwd=Path.cwd(), capture_output=True, text=True)

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

    def write_config(self, path, repo, *, project_id="example-project", allowed_paths=(), validation_commands=("git diff --check",), reviewer="git"):
        config = {
            "project": {
                "id": project_id,
                "primary_repository_path": str(repo),
                "default_branch": "main",
                "allowed_primary_local_paths": list(allowed_paths),
            },
            "roles": {"implementer": "git", "reviewer": reviewer},
            "execution_policy": {
                "schema_version": "1",
                "publication": {"merge_strategy": "merge"},
                "review": {"reviewer": reviewer, "max_rounds": 5},
                "cleanup": {"autonomous": True},
                "guardian": {"stop_on_uncertain": True},
                "validation": {"commands": list(validation_commands)},
            },
        }
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(config), encoding="utf-8")
        return path

    def write_archive(
        self,
        repo,
        *,
        spec="012-status-foundation",
        validated_sha="",
        approved_review_sha="",
        decision="Approved",
        merge_commit="",
        pull_request=0,
    ):
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
                    "pull_request": pull_request,
                }
            ),
            encoding="utf-8",
        )
        return archive

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
        self.config = self.test.write_config(self.root / "project-config.json", self.repo, **self.kwargs)
        return self

    def __exit__(self, exc_type, exc, traceback):
        self.temp.cleanup()


if __name__ == "__main__":
    unittest.main()

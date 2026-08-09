import contextlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from ados.cli import main
from ados.doctor import DoctorRequest, DoctorService


class CliDoctorTests(unittest.TestCase):
    def test_root_help(self):
        completed = self.run_module("--help")

        self.assertEqual(0, completed.returncode)
        self.assertIn("doctor", completed.stdout)

    def test_version_output(self):
        completed = self.run_module("--version")

        self.assertEqual(0, completed.returncode)
        self.assertIn("ados 1.0.0", completed.stdout)

    def test_doctor_ready_exit_zero_for_valid_temporary_project(self):
        with self.project() as fixture:
            code, stdout = self.run_cli("doctor", "--project", str(fixture.repo), "--config", str(fixture.config))

        self.assertEqual(0, code)
        self.assertIn("READY", stdout)
        self.assertNotIn("Traceback", stdout)

    def test_dirty_primary_blocks_exit_one(self):
        with self.project() as fixture:
            (fixture.repo / "README.md").write_text("dirty\n", encoding="utf-8")
            code, stdout = self.run_cli("doctor", str(fixture.repo), "--config", str(fixture.config))

        self.assertEqual(1, code)
        self.assertIn("BLOCKED", stdout)
        self.assertIn("DIRTY_TRACKED_FILES", stdout)
        self.assertNotIn("Traceback", stdout)

    def test_invalid_config_exits_two(self):
        with self.project() as fixture:
            fixture.config.write_text("{ bad json", encoding="utf-8")
            code, stdout = self.run_cli("doctor", "--project", str(fixture.repo), "--config", str(fixture.config), "--json")

        self.assertEqual(2, code)
        result = json.loads(stdout)
        self.assertEqual("INVALID", result["status"])

    def test_missing_path_exits_two(self):
        with tempfile.TemporaryDirectory() as directory:
            missing = Path(directory) / "missing"
            config = Path(directory) / "config.json"
            config.write_text("{}", encoding="utf-8")
            code, _stdout = self.run_cli("doctor", "--project", str(missing), "--config", str(config))

        self.assertEqual(2, code)

    def test_non_git_project_blocks(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            project = root / "not git"
            project.mkdir()
            config = self.write_config(root / "config.json", project)
            code, stdout = self.run_cli("doctor", "--project", str(project), "--config", str(config))

        self.assertEqual(1, code)
        self.assertIn("NOT_GIT_REPOSITORY", stdout)

    def test_allowed_local_path_passes(self):
        with self.project(allowed_paths=[".claude"]) as fixture:
            (fixture.repo / ".claude").mkdir()
            (fixture.repo / ".claude" / "settings.json").write_text("{}", encoding="utf-8")
            code, _stdout = self.run_cli("doctor", "--project", str(fixture.repo), "--config", str(fixture.config))

        self.assertEqual(0, code)

    def test_unexpected_untracked_path_blocks(self):
        with self.project() as fixture:
            (fixture.repo / "scratch.txt").write_text("scratch\n", encoding="utf-8")
            code, stdout = self.run_cli("doctor", "--project", str(fixture.repo), "--config", str(fixture.config))

        self.assertEqual(1, code)
        self.assertIn("UNEXPECTED_UNTRACKED_FILES", stdout)

    def test_execution_policy_validation_and_merge_strategy(self):
        with self.project(merge_strategy="squash") as fixture:
            result = DoctorService().run(DoctorRequest(fixture.repo, fixture.config))

        checks = {check.id: check for check in result.checks}
        self.assertEqual("PASS", checks["execution_policy"].status)
        self.assertEqual("squash", checks["publication_policy"].evidence["merge_strategy"])

    def test_implementer_executable_available_and_unavailable(self):
        with self.project(implementer="git") as fixture:
            ready = DoctorService().run(DoctorRequest(fixture.repo, fixture.config))
        with self.project(implementer="ados-missing-implementer") as fixture:
            blocked = DoctorService().run(DoctorRequest(fixture.repo, fixture.config))

        self.assertEqual("PASS", self.check(ready, "implementer_adapter").status)
        self.assertEqual("FAIL", self.check(blocked, "implementer_adapter").status)
        self.assertEqual("BLOCKED", blocked.status)

    def test_reviewer_executable_available_and_unavailable(self):
        with self.project(reviewer="git") as fixture:
            ready = DoctorService().run(DoctorRequest(fixture.repo, fixture.config))
        with self.project(reviewer="ados-missing-reviewer") as fixture:
            blocked = DoctorService().run(DoctorRequest(fixture.repo, fixture.config))

        self.assertEqual("PASS", self.check(ready, "reviewer_adapter").status)
        self.assertEqual("FAIL", self.check(blocked, "reviewer_adapter").status)

    def test_unsafe_configured_runner_is_not_executed(self):
        with self.project(implementer=f"{sys.executable} && ados-should-not-run") as fixture:
            result = DoctorService().run(DoctorRequest(fixture.repo, fixture.config))

        check = self.check(result, "implementer_adapter")
        self.assertEqual("FAIL", check.status)
        self.assertEqual("ADAPTER_COMMAND_UNSAFE", check.violations[0].code)

    def test_json_output_schema_and_violation_evidence(self):
        with self.project() as fixture:
            (fixture.repo / "scratch.txt").write_text("scratch\n", encoding="utf-8")
            code, stdout = self.run_cli("doctor", "--project", str(fixture.repo), "--config", str(fixture.config), "--json")

        self.assertEqual(1, code)
        result = json.loads(stdout)
        self.assertEqual("BLOCKED", result["status"])
        guardian = next(check for check in result["checks"] if check["id"] == "primary_repository_guardian")
        self.assertEqual("FAIL", guardian["status"])
        self.assertIn("files", guardian["violations"][0]["evidence"])

    def test_windows_path_with_spaces(self):
        with tempfile.TemporaryDirectory(prefix="ados path with spaces ") as directory:
            root = Path(directory)
            repo = root / "project repo"
            self.init_repo(repo)
            config = self.write_config(root / "config.json", repo)
            code, _stdout = self.run_cli("doctor", "--project", str(repo), "--config", str(config))

        self.assertEqual(0, code)

    def test_doctor_is_side_effect_free(self):
        with self.project() as fixture:
            before = self.snapshot(fixture.repo)
            first = DoctorService().run(DoctorRequest(fixture.repo, fixture.config))
            second = DoctorService().run(DoctorRequest(fixture.repo, fixture.config))
            after = self.snapshot(fixture.repo)

        self.assertEqual("READY", first.status)
        self.assertEqual("READY", second.status)
        self.assertEqual(before, after)

    def test_no_validation_reviewer_publication_or_worktree_mutation(self):
        with self.project(validation_commands=["python -c \"open('validation-ran.txt','w').write('bad')\""], reviewer=f"{sys.executable} -c \"open('review-ran.txt','w').write('bad')\"") as fixture:
            before_worktrees = self.git(fixture.repo, "worktree", "list", "--porcelain").stdout
            result = DoctorService().run(DoctorRequest(fixture.repo, fixture.config))
            after_worktrees = self.git(fixture.repo, "worktree", "list", "--porcelain").stdout

            self.assertEqual("READY", result.status)
            self.assertFalse((fixture.repo / "validation-ran.txt").exists())
            self.assertFalse((fixture.repo / "review-ran.txt").exists())
            self.assertEqual(before_worktrees, after_worktrees)

    def test_project_neutral_behavior(self):
        with self.project(project_id="other-project") as fixture:
            result = DoctorService().run(DoctorRequest(fixture.repo, fixture.config))

        self.assertEqual("READY", result.status)
        self.assertEqual("other-project", self.check(result, "project_configuration").evidence["project_id"])

    def test_discovered_project_config(self):
        with self.project(config_inside_repo=True) as fixture:
            code, _stdout = self.run_cli("doctor", "--project", str(fixture.repo))

        self.assertEqual(0, code)

    def test_aiverse_config_fixture_can_drive_doctor_without_modifying_aiverse_shape(self):
        with self.project(project_id="aiverse", allowed_paths=[".claude"], validation_commands=["npm test", "git diff --check"]) as fixture:
            result = DoctorService().run(DoctorRequest(fixture.repo, fixture.config))

        self.assertEqual("READY", result.status)
        self.assertEqual("aiverse", self.check(result, "project_configuration").evidence["project_id"])

    def test_project_config_roles_are_optional_and_serializable(self):
        with self.project(implementer=None) as fixture:
            result = DoctorService().run(DoctorRequest(fixture.repo, fixture.config))

        self.assertEqual("WARN", self.check(result, "implementer_adapter").status)
        self.assertEqual("READY", result.status)

    def run_module(self, *args):
        return subprocess.run((sys.executable, "-m", "ados", *args), cwd=Path.cwd(), capture_output=True, text=True)

    def run_cli(self, *args):
        stream = io.StringIO()
        with contextlib.redirect_stdout(stream):
            code = main(list(args))
        return code, stream.getvalue()

    def check(self, result, check_id):
        return next(check for check in result.checks if check.id == check_id)

    def project(self, **kwargs):
        return TemporaryProject(self, **kwargs)

    def init_repo(self, repo):
        repo.mkdir(parents=True)
        self.git(repo, "init", "-b", "main")
        self.git(repo, "config", "user.email", "test@example.invalid")
        self.git(repo, "config", "user.name", "Test User")
        (repo / "README.md").write_text("base\n", encoding="utf-8")
        self.git(repo, "add", "README.md")
        self.git(repo, "commit", "-m", "initial")

    def write_config(
        self,
        path,
        repo,
        *,
        project_id="example-project",
        allowed_paths=(),
        validation_commands=("git diff --check",),
        merge_strategy="merge",
        implementer="git",
        reviewer="git",
    ):
        config = {
            "project": {
                "id": project_id,
                "primary_repository_path": str(repo),
                "default_branch": "main",
                "allowed_primary_local_paths": list(allowed_paths),
            },
            "execution_policy": {
                "schema_version": "1",
                "publication": {"merge_strategy": merge_strategy},
                "review": {"reviewer": reviewer or "git", "max_rounds": 5},
                "cleanup": {"autonomous": True},
                "guardian": {"stop_on_uncertain": True},
                "validation": {"commands": list(validation_commands)},
            },
        }
        roles = {}
        if implementer is not None:
            roles["implementer"] = implementer
        if reviewer is not None:
            roles["reviewer"] = reviewer
        if roles:
            config["roles"] = roles
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(config), encoding="utf-8")
        return path

    def git(self, repo, *args):
        return subprocess.run(("git", *args), cwd=repo, check=True, capture_output=True, text=True)

    def snapshot(self, repo):
        return {
            "head": self.git(repo, "rev-parse", "HEAD").stdout,
            "status": self.git(repo, "status", "--short").stdout,
            "files": sorted(str(path.relative_to(repo)) for path in repo.rglob("*") if ".git" not in path.parts),
        }


class TemporaryProject:
    def __init__(self, test, **kwargs):
        self.test = test
        self.kwargs = kwargs

    def __enter__(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.repo = self.root / "project"
        self.test.init_repo(self.repo)
        config_inside_repo = self.kwargs.pop("config_inside_repo", False)
        config_path = self.repo / ".ados" / "project-config.json" if config_inside_repo else self.root / "project-config.json"
        self.config = self.test.write_config(config_path, self.repo, **self.kwargs)
        if config_inside_repo:
            self.test.git(self.repo, "add", ".ados/project-config.json")
            self.test.git(self.repo, "commit", "-m", "add project config")
        return self

    def __exit__(self, exc_type, exc, traceback):
        self.temp.cleanup()


if __name__ == "__main__":
    unittest.main()

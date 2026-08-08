import subprocess
import tempfile
import unittest
from pathlib import Path

from ados.execution_policy import ExecutionPolicy
from ados.validation_engine import ValidationEngine


class ValidationEngineTests(unittest.TestCase):
    def policy(self, commands):
        return ExecutionPolicy.from_mapping(
            {
                "execution_policy": {
                    "schema_version": "1",
                    "publication": {"merge_strategy": "merge"},
                    "review": {"reviewer": "reviewer", "max_rounds": 5},
                    "cleanup": {"autonomous": True},
                    "guardian": {"stop_on_uncertain": True},
                    "validation": {"commands": commands},
                }
            }
        )

    def test_validation_passes_for_zero_exit_commands(self):
        with self.repo() as repo:
            result = ValidationEngine().run(policy=self.policy(["git status --short"]), repository_path=repo)

        self.assertEqual("PASS", result.status)
        self.assertEqual(result.head_before, result.head_after)
        self.assertEqual(0, result.commands[0].exit_code)

    def test_validation_blocks_on_nonzero_command(self):
        with self.repo() as repo:
            result = ValidationEngine().run(policy=self.policy(["python -c \"import sys; sys.exit(7)\""]), repository_path=repo)

        self.assertEqual("BLOCK", result.status)
        self.assertEqual("VALIDATION_COMMAND_FAILED", result.violations[0].code)

    def test_validation_blocks_on_head_drift(self):
        with self.repo() as repo:
            command = "git commit --allow-empty -m validation-drift"
            result = ValidationEngine().run(policy=self.policy([command]), repository_path=repo)

        self.assertEqual("BLOCK", result.status)
        self.assertIn("VALIDATION_HEAD_DRIFT", {violation.code for violation in result.violations})

    def repo(self):
        return TemporaryGitRepository()


class TemporaryGitRepository:
    def __enter__(self):
        self.directory = tempfile.TemporaryDirectory()
        self.path = Path(self.directory.name)
        self.git("init")
        self.git("config", "user.email", "test@example.invalid")
        self.git("config", "user.name", "Test User")
        (self.path / "README.md").write_text("base\n", encoding="utf-8")
        self.git("add", "README.md")
        self.git("commit", "-m", "initial")
        return self.path

    def __exit__(self, exc_type, exc, traceback):
        self.directory.cleanup()

    def git(self, *args):
        return subprocess.run(("git", *args), cwd=self.path, check=True, capture_output=True, text=True)


if __name__ == "__main__":
    unittest.main()

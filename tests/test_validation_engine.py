import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from ados.execution_policy import ExecutionPolicy
from ados.validation_engine import ValidationEngine, _bounded


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
        self.assertEqual("python -c \"import sys; sys.exit(7)\"", result.commands[0].command)
        self.assertEqual(7, result.commands[0].exit_code)

    def test_validation_blocks_on_head_drift(self):
        with self.repo() as repo:
            command = "git commit --allow-empty -m validation-drift"
            result = ValidationEngine().run(policy=self.policy([command]), repository_path=repo)

        self.assertEqual("BLOCK", result.status)
        self.assertIn("VALIDATION_HEAD_DRIFT", {violation.code for violation in result.violations})

    def test_validation_capture_uses_utf8_replace_and_bounds_output(self):
        with self.repo() as repo:
            result = ValidationEngine().run(
                policy=self.policy(
                    [
                        "python -c \"import sys; sys.stdout.buffer.write('snowman ☃ emoji 🚀'.encode('utf-8')); sys.stderr.buffer.write(b'bad-\\x9d-byte'); sys.exit(3)\""
                    ]
                ),
                repository_path=repo,
            )

        self.assertEqual("BLOCK", result.status)
        self.assertIn("snowman", result.commands[0].stdout)
        self.assertIn("🚀", result.commands[0].stdout)
        self.assertIn("�", result.commands[0].stderr)
        self.assertIn("bad-", result.commands[0].stderr)

    def test_validation_subprocess_uses_explicit_encoding_and_shell_contract(self):
        with self.repo() as repo:
            calls = []

            def fake_run(*args, **kwargs):
                calls.append((args, kwargs))
                return subprocess.CompletedProcess(args[0], 0, "ok", "")

            with mock.patch("ados.validation_engine.subprocess.run", side_effect=fake_run):
                result = ValidationEngine().run(policy=self.policy(["safe command"]), repository_path=repo)

        validation_call = next(call for call in calls if call[0][0] == "safe command")[1]
        self.assertEqual("utf-8", validation_call["encoding"])
        self.assertEqual("replace", validation_call["errors"])
        self.assertIs(validation_call["shell"], True)
        self.assertEqual("PASS", result.status)

    def test_bounded_handles_none_bytes_and_truncation(self):
        self.assertEqual("", _bounded(None))
        self.assertEqual("plain", _bounded("plain"))
        self.assertEqual("abc", _bounded(b"abc"))
        self.assertEqual("�", _bounded(b"\x9d"))
        self.assertEqual(20_000, len(_bounded("x" * 20_001)))

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

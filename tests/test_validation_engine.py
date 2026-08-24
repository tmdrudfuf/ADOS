import subprocess
import sys
import time
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from ados.execution_policy import ExecutionPolicy
from ados.validation_engine import ValidationEngine, _bounded


class ValidationEngineTests(unittest.TestCase):
    def policy(self, commands, timeout_ms=None):
        validation = {"commands": commands}
        if timeout_ms is not None:
            validation["timeout_ms"] = timeout_ms
        return ExecutionPolicy.from_mapping(
            {
                "execution_policy": {
                    "schema_version": "1",
                    "publication": {"merge_strategy": "merge"},
                    "review": {"reviewer": "reviewer", "max_rounds": 5},
                    "cleanup": {"autonomous": True},
                    "guardian": {"stop_on_uncertain": True},
                    "validation": validation,
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

            class FakeProcess:
                returncode = 0
                pid = 12345

                def communicate(self, timeout=None):
                    return "ok", ""

            def fake_popen(*args, **kwargs):
                calls.append((args, kwargs))
                return FakeProcess()

            with mock.patch("ados.validation_engine.subprocess.Popen", side_effect=fake_popen):
                result = ValidationEngine(provider=StaticHeadProvider()).run(policy=self.policy(["safe command"]), repository_path=repo)

        validation_call = next(call for call in calls if call[0][0] == "safe command")[1]
        self.assertEqual("utf-8", validation_call["encoding"])
        self.assertEqual("replace", validation_call["errors"])
        self.assertIs(validation_call["shell"], True)
        self.assertEqual("PASS", result.status)

    def test_validation_timeout_records_evidence_and_stops_later_commands(self):
        with self.repo() as repo:
            marker = repo / "should-not-run.txt"
            timeout_command = f'"{sys.executable}" -c "import sys, time; print(\'before timeout\', flush=True); print(\'stderr before timeout\', file=sys.stderr, flush=True); time.sleep(30)"'
            next_command = f'"{sys.executable}" -c "from pathlib import Path; Path(r\'{marker}\').write_text(\'ran\', encoding=\'utf-8\')"'
            result = ValidationEngine().run(policy=self.policy([timeout_command, next_command], timeout_ms=200), repository_path=repo)
            marker_exists = marker.exists()

        self.assertEqual("BLOCK", result.status)
        self.assertEqual("VALIDATION_COMMAND_TIMED_OUT", result.violations[0].code)
        self.assertEqual(1, len(result.commands))
        self.assertTrue(result.commands[0].timed_out)
        self.assertIn("before timeout", result.commands[0].stdout)
        self.assertIn("stderr before timeout", result.commands[0].stderr)
        self.assertFalse(marker_exists)

    def test_timed_out_validation_kills_descendant_process(self):
        with self.repo() as repo:
            marker = repo / "descendant-survived.txt"
            child = repo / "child.py"
            parent = repo / "parent.py"
            child.write_text(
                "from pathlib import Path\n"
                "import sys, time\n"
                "time.sleep(2)\n"
                "Path(sys.argv[1]).write_text('survived', encoding='utf-8')\n",
                encoding="utf-8",
            )
            parent.write_text(
                "import subprocess, sys, time\n"
                "subprocess.Popen([sys.executable, sys.argv[1], sys.argv[2]])\n"
                "print('parent ready', flush=True)\n"
                "time.sleep(30)\n",
                encoding="utf-8",
            )
            command = f'"{sys.executable}" "{parent}" "{child}" "{marker}"'
            result = ValidationEngine().run(policy=self.policy([command], timeout_ms=300), repository_path=repo)
            time.sleep(3)
            marker_exists = marker.exists()

        self.assertEqual("BLOCK", result.status)
        self.assertTrue(result.commands[0].timed_out)
        self.assertFalse(marker_exists)

    def test_validation_timeout_does_not_kill_unrelated_external_process(self):
        with self.repo() as repo:
            marker = repo / "external-survived.txt"
            external_script = repo / "external.py"
            external_script.write_text(
                "from pathlib import Path\n"
                "import sys, time\n"
                "time.sleep(1)\n"
                "Path(sys.argv[1]).write_text('ok', encoding='utf-8')\n",
                encoding="utf-8",
            )
            external = subprocess.Popen(
                (sys.executable, str(external_script), str(marker)),
                cwd=repo,
            )
            try:
                command = f'"{sys.executable}" -c "import time; time.sleep(30)"'
                result = ValidationEngine().run(policy=self.policy([command], timeout_ms=200), repository_path=repo)
                external.wait(timeout=5)
                marker_exists = marker.exists()
            finally:
                if external.poll() is None:
                    external.kill()

        self.assertEqual("BLOCK", result.status)
        self.assertTrue(result.commands[0].timed_out)
        self.assertTrue(marker_exists)

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


class StaticHeadProvider:
    def current_head(self, repo):
        return "a" * 40


if __name__ == "__main__":
    unittest.main()

import tempfile
import unittest
from pathlib import Path

from ados.execution_policy import ExecutionPolicy
from ados.primary_repository_guardian import PrimaryRepositoryGuardian
from ados.repository_provider import RepositoryProviderError, RepositoryStatus


class FakeProvider:
    def __init__(self, status=None, error=None):
        self._status = status
        self._error = error

    def status(self, path):
        if self._error:
            raise self._error
        return self._status


class PrimaryRepositoryGuardianTests(unittest.TestCase):
    def policy(self, stop_on_uncertain=True):
        return ExecutionPolicy.from_mapping(
            {
                "execution_policy": {
                    "schema_version": "1",
                    "publication": {"merge_strategy": "merge"},
                    "review": {"reviewer": "reviewer", "max_rounds": 5},
                    "cleanup": {"autonomous": True},
                    "guardian": {"stop_on_uncertain": stop_on_uncertain},
                    "validation": {"commands": ["git diff --check"]},
                }
            }
        )

    def status(self, **overrides):
        values = {
            "root": Path("/repo").resolve(),
            "branch": "main",
            "head": "abc123",
            "staged": (),
            "dirty_tracked": (),
            "untracked": (),
        }
        values.update(overrides)
        return RepositoryStatus(**values)

    def test_clean_repository_passes(self):
        guardian = PrimaryRepositoryGuardian(FakeProvider(self.status()))

        result = guardian.audit(
            policy=self.policy(),
            repository_path="/repo",
            expected_repository_path="/repo",
            expected_branch="main",
            expected_head="abc123",
        )

        self.assertEqual("PASS", result.status)
        self.assertEqual((), result.violations)

    def test_detects_staged_dirty_untracked_branch_and_head(self):
        guardian = PrimaryRepositoryGuardian(
            FakeProvider(
                self.status(
                    branch="feature",
                    head="def456",
                    staged=("a.txt",),
                    dirty_tracked=("b.txt",),
                    untracked=("c.txt",),
                )
            )
        )

        result = guardian.audit(
            policy=self.policy(),
            repository_path="/repo",
            expected_branch="main",
            expected_head="abc123",
        )

        codes = {violation.code for violation in result.violations}
        self.assertEqual("BLOCK", result.status)
        self.assertIn("BRANCH_MISMATCH", codes)
        self.assertIn("HEAD_MISMATCH", codes)
        self.assertIn("STAGED_FILES", codes)
        self.assertIn("DIRTY_TRACKED_FILES", codes)
        self.assertIn("UNEXPECTED_UNTRACKED_FILES", codes)

    def test_allowed_local_path_suppresses_untracked_violation(self):
        guardian = PrimaryRepositoryGuardian(
            FakeProvider(self.status(untracked=(".claude/settings.json",)))
        )

        result = guardian.audit(
            policy=self.policy(),
            repository_path="/repo",
            allowed_local_paths=(".claude",),
        )

        self.assertEqual("PASS", result.status)

    def test_repository_provider_error_blocks(self):
        guardian = PrimaryRepositoryGuardian(
            FakeProvider(error=RepositoryProviderError("NOT_GIT_REPOSITORY", "not a git repo"))
        )

        result = guardian.audit(policy=self.policy(), repository_path="/not-git")

        self.assertEqual("BLOCK", result.status)
        self.assertEqual("NOT_GIT_REPOSITORY", result.violations[0].code)

    def test_repository_mismatch_blocks(self):
        guardian = PrimaryRepositoryGuardian(FakeProvider(self.status(root=Path("/actual").resolve())))

        result = guardian.audit(
            policy=self.policy(),
            repository_path="/actual",
            expected_repository_path="/expected",
        )

        self.assertEqual("BLOCK", result.status)
        self.assertEqual("REPOSITORY_MISMATCH", result.violations[0].code)

    def test_stop_on_uncertain_false_blocks(self):
        guardian = PrimaryRepositoryGuardian(FakeProvider(self.status()))

        result = guardian.audit(policy=self.policy(stop_on_uncertain=False), repository_path="/repo")

        self.assertEqual("BLOCK", result.status)
        self.assertEqual("GUARDIAN_UNCERTAIN_ALLOWED", result.violations[0].code)

    def test_real_non_git_directory_blocks(self):
        with tempfile.TemporaryDirectory() as directory:
            result = PrimaryRepositoryGuardian().audit(
                policy=self.policy(),
                repository_path=directory,
            )

        self.assertEqual("BLOCK", result.status)
        self.assertEqual("NOT_GIT_REPOSITORY", result.violations[0].code)


if __name__ == "__main__":
    unittest.main()

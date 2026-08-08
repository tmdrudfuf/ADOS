import unittest
from pathlib import Path

from ados.primary_repository_guardian import PrimaryRepositoryGuardian
from ados.project_config import load_project_config
from ados.repository_provider import RepositoryStatus


class FakeProvider:
    def __init__(self, status):
        self._status = status

    def status(self, path):
        return self._status


class AIverseIntegrationTests(unittest.TestCase):
    def test_aiverse_config_loads_through_project_config_model(self):
        config = load_project_config("tests/fixtures/aiverse-project-config.valid.json")

        self.assertEqual("aiverse", config.project_id)
        self.assertEqual("main", config.default_branch)
        self.assertEqual((".claude", ".serena"), config.allowed_primary_local_paths)
        self.assertEqual("merge", config.execution_policy.publication.merge_strategy)
        self.assertEqual("claude -p", config.execution_policy.review.reviewer)
        self.assertEqual(5, config.execution_policy.review.max_rounds)
        self.assertIn("npm run build", config.execution_policy.validation.commands)

    def test_aiverse_config_drives_guardian_inputs(self):
        config = load_project_config("tests/fixtures/aiverse-project-config.valid.json")
        head = "abc123"
        status = RepositoryStatus(
            root=Path(config.primary_repository_path).resolve(),
            branch=config.default_branch,
            head=head,
            staged=(),
            dirty_tracked=(),
            untracked=(".claude/settings.json", ".serena/project.yml"),
        )

        result = PrimaryRepositoryGuardian(FakeProvider(status)).audit(
            policy=config.execution_policy,
            repository_path=config.primary_repository_path,
            expected_repository_path=config.primary_repository_path,
            expected_branch=config.default_branch,
            expected_head=head,
            allowed_local_paths=config.allowed_primary_local_paths,
        )

        self.assertEqual("PASS", result.status)


if __name__ == "__main__":
    unittest.main()

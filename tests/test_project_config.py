import dataclasses
import json
from pathlib import Path
import unittest

from ados.project_config import ProjectConfig, ProjectConfigError, load_project_config


class ProjectConfigTests(unittest.TestCase):
    def valid_mapping(self):
        return json.loads(Path("tests/fixtures/project-config.valid.json").read_text(encoding="utf-8"))

    def test_valid_config_is_immutable_and_reuses_execution_policy(self):
        config = ProjectConfig.from_mapping(self.valid_mapping())

        self.assertTrue(dataclasses.is_dataclass(config))
        self.assertEqual("example-project", config.project_id)
        self.assertEqual((".claude",), config.allowed_primary_local_paths)
        self.assertEqual("merge", config.execution_policy.publication.merge_strategy)
        with self.assertRaises(dataclasses.FrozenInstanceError):
            config.project_id = "changed"

    def test_missing_project_is_deterministic_error(self):
        with self.assertRaises(ProjectConfigError) as context:
            ProjectConfig.from_mapping({"execution_policy": {}})

        self.assertEqual("PROJECT_CONFIG_MISSING_PROJECT", context.exception.code)

    def test_embedded_policy_error_is_deterministic(self):
        raw = self.valid_mapping()
        raw["execution_policy"]["publication"]["merge_strategy"] = "default"

        with self.assertRaises(ProjectConfigError) as context:
            ProjectConfig.from_mapping(raw)

        self.assertEqual("POLICY_INVALID_MERGE_STRATEGY", context.exception.code)

    def test_load_project_config(self):
        config = load_project_config("tests/fixtures/project-config.valid.json")

        self.assertEqual("main", config.default_branch)


if __name__ == "__main__":
    unittest.main()

import dataclasses
import json
import unittest

from ados.execution_policy import ExecutionPolicy, PolicyValidationError, load_execution_policy


class ExecutionPolicyTests(unittest.TestCase):
    def valid_mapping(self):
        return {
            "execution_policy": {
                "schema_version": "1",
                "publication": {"merge_strategy": "merge"},
                "review": {"reviewer": "reviewer", "max_rounds": 5},
                "cleanup": {"autonomous": True},
                "guardian": {"stop_on_uncertain": True},
                "validation": {"commands": ["git diff --check"]},
            }
        }

    def test_valid_policy_is_immutable_and_serializable(self):
        policy = ExecutionPolicy.from_mapping(self.valid_mapping())

        self.assertTrue(dataclasses.is_dataclass(policy))
        self.assertEqual("merge", policy.publication.merge_strategy)
        self.assertEqual(("git diff --check",), policy.validation.commands)
        self.assertEqual(1_800_000, policy.validation.timeout_ms)
        self.assertEqual(3, policy.validation.max_recovery_rounds)
        with self.assertRaises(dataclasses.FrozenInstanceError):
            policy.schema_version = "2"

        serialized = json.loads(policy.to_json())
        self.assertEqual("merge", serialized["publication"]["merge_strategy"])

    def test_missing_root_is_deterministic_error(self):
        with self.assertRaises(PolicyValidationError) as context:
            ExecutionPolicy.from_mapping({})

        self.assertEqual("POLICY_MISSING_ROOT", context.exception.code)

    def test_invalid_merge_strategy_is_rejected(self):
        raw = self.valid_mapping()
        raw["execution_policy"]["publication"]["merge_strategy"] = "default"

        with self.assertRaises(PolicyValidationError) as context:
            ExecutionPolicy.from_mapping(raw)

        self.assertEqual("POLICY_INVALID_MERGE_STRATEGY", context.exception.code)

    def test_policy_file_loads(self):
        policy = load_execution_policy("tests/fixtures/execution-policy.valid.json")

        self.assertEqual("1", policy.schema_version)

    def test_validation_timeout_is_optional_and_positive_when_present(self):
        raw = self.valid_mapping()
        raw["execution_policy"]["validation"]["timeout_ms"] = 1234
        policy = ExecutionPolicy.from_mapping(raw)

        self.assertEqual(1234, policy.validation.timeout_ms)

        raw["execution_policy"]["validation"]["timeout_ms"] = 0
        with self.assertRaises(PolicyValidationError) as context:
            ExecutionPolicy.from_mapping(raw)

        self.assertEqual("POLICY_INVALID_VALIDATION_TIMEOUT_MS", context.exception.code)

    def test_validation_max_recovery_rounds_is_optional_and_positive_when_present(self):
        raw = self.valid_mapping()
        raw["execution_policy"]["validation"]["max_recovery_rounds"] = 2
        policy = ExecutionPolicy.from_mapping(raw)

        self.assertEqual(2, policy.validation.max_recovery_rounds)

        raw["execution_policy"]["validation"]["max_recovery_rounds"] = 0
        with self.assertRaises(PolicyValidationError) as context:
            ExecutionPolicy.from_mapping(raw)

        self.assertEqual("POLICY_INVALID_VALIDATION_MAX_RECOVERY_ROUNDS", context.exception.code)


if __name__ == "__main__":
    unittest.main()

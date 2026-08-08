import unittest

from ados.recovery_engine import RecoveryEngine, RecoveryIssue


class RecoveryEngineTests(unittest.TestCase):
    def issue(self, code):
        return RecoveryIssue("test", code, "message", {})

    def test_no_issues_continues(self):
        result = RecoveryEngine().classify(())

        self.assertEqual("RECOVERABLE", result.status)
        self.assertEqual("continue_workflow", result.action)

    def test_primary_repository_issue_requires_human(self):
        result = RecoveryEngine().classify((self.issue("DIRTY_TRACKED_FILES"),))

        self.assertEqual("HUMAN_INTERVENTION_REQUIRED", result.status)
        self.assertEqual("inspect_primary_repository_state", result.action)

    def test_validation_failure_is_recoverable(self):
        result = RecoveryEngine().classify((self.issue("VALIDATION_COMMAND_FAILED"),))

        self.assertEqual("RECOVERABLE", result.status)
        self.assertEqual("fix_validation_failures_then_revalidate", result.action)

    def test_changes_requested_enters_fix_loop(self):
        result = RecoveryEngine().classify((self.issue("CHANGES_REQUESTED"),))

        self.assertEqual("RECOVERABLE", result.status)
        self.assertEqual("fix_valid_blocking_findings_then_revalidate", result.action)

    def test_sha_mismatch_repeats_validation_and_review(self):
        result = RecoveryEngine().classify((self.issue("SHA_MISMATCH"),))

        self.assertEqual("RECOVERABLE", result.status)
        self.assertEqual("repeat_validation_and_independent_review", result.action)

    def test_publication_gate_failure_requires_human(self):
        result = RecoveryEngine().classify((self.issue("PR_NOT_MERGEABLE"),))

        self.assertEqual("HUMAN_INTERVENTION_REQUIRED", result.status)
        self.assertEqual("resolve_publication_blocker", result.action)

    def test_unknown_condition_requires_human(self):
        result = RecoveryEngine().classify((self.issue("UNKNOWN"),))

        self.assertEqual("HUMAN_INTERVENTION_REQUIRED", result.status)
        self.assertEqual("unknown_recovery_condition", result.action)


if __name__ == "__main__":
    unittest.main()

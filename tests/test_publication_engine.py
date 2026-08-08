import unittest

from ados.execution_policy import ExecutionPolicy
from ados.publication_engine import PublicationEngine, PublicationEvidence


class PublicationEngineTests(unittest.TestCase):
    def policy(self):
        return ExecutionPolicy.from_mapping(
            {
                "execution_policy": {
                    "schema_version": "1",
                    "publication": {"merge_strategy": "merge"},
                    "review": {"reviewer": "reviewer", "max_rounds": 5},
                    "cleanup": {"autonomous": True},
                    "guardian": {"stop_on_uncertain": True},
                    "validation": {"commands": ["git diff --check"]},
                }
            }
        )

    def evidence(self, **overrides):
        values = {
            "review_decision": "Approved",
            "blocking_findings": (),
            "validation_passed": True,
            "approved_review_sha": "sha",
            "validated_sha": "sha",
            "local_head_sha": "sha",
            "remote_branch_head_sha": "sha",
            "pr_head_sha": "sha",
            "exact_head_gate": "MATCH",
            "primary_repository_audit": "SAFE",
            "feature_worktree_clean": True,
            "intended_base_branch": "main",
            "intended_head_branch": "feature",
            "pr_base_branch": "main",
            "pr_head_branch": "feature",
            "pr_mergeable": True,
            "unresolved_blocking_review_state": False,
            "post_approval_commit": False,
            "safety_recovery_active": False,
            "scope_approved": True,
            "merge_strategy": "merge",
            "force_push_required": False,
            "history_rewrite_required": False,
            "bypass_required": False,
        }
        values.update(overrides)
        return PublicationEvidence(**values)

    def test_permitted_when_all_gates_pass(self):
        result = PublicationEngine().evaluate(policy=self.policy(), evidence=self.evidence())

        self.assertEqual("PERMITTED", result.status)
        self.assertEqual((), result.violations)

    def test_sha_mismatch_requires_human(self):
        result = PublicationEngine().evaluate(
            policy=self.policy(),
            evidence=self.evidence(pr_head_sha="other"),
        )

        self.assertEqual("HUMAN_INTERVENTION_REQUIRED", result.status)
        self.assertEqual("SHA_MISMATCH", result.violations[0].code)
        self.assertEqual("sha", result.violations[0].evidence["approved_review_sha"])
        self.assertEqual("other", result.violations[0].evidence["pr_head_sha"])

    def test_merge_strategy_mismatch_requires_human(self):
        result = PublicationEngine().evaluate(
            policy=self.policy(),
            evidence=self.evidence(merge_strategy="squash"),
        )

        self.assertEqual("HUMAN_INTERVENTION_REQUIRED", result.status)
        self.assertEqual("MERGE_STRATEGY_MISMATCH", result.violations[0].code)

    def test_multiple_failures_are_reported(self):
        result = PublicationEngine().evaluate(
            policy=self.policy(),
            evidence=self.evidence(review_decision="Changes Requested", pr_mergeable=False),
        )

        codes = {violation.code for violation in result.violations}
        self.assertIn("REVIEW_NOT_APPROVED", codes)
        self.assertIn("PR_NOT_MERGEABLE", codes)


if __name__ == "__main__":
    unittest.main()

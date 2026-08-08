import sys
import unittest

from ados.execution_policy import ExecutionPolicy
from ados.review_engine import ReviewEngine, ReviewRequest, parse_review_decision


class ReviewEngineTests(unittest.TestCase):
    def policy(self, reviewer_command):
        return ExecutionPolicy.from_mapping(
            {
                "execution_policy": {
                    "schema_version": "1",
                    "publication": {"merge_strategy": "merge"},
                    "review": {"reviewer": reviewer_command, "max_rounds": 5},
                    "cleanup": {"autonomous": True},
                    "guardian": {"stop_on_uncertain": True},
                    "validation": {"commands": ["git diff --check"]},
                }
            }
        )

    def request(self):
        return ReviewRequest(candidate_sha="abc123", base_sha="base123", scope="test", diff="diff")

    def test_parse_approved(self):
        self.assertEqual("Approved", parse_review_decision("**Approved**\nReviewed SHA: abc123"))

    def test_parse_approved_with_markdown_and_whitespace(self):
        self.assertEqual("Approved", parse_review_decision("  **Approved**  "))

    def test_parse_changes_requested(self):
        self.assertEqual("Changes Requested", parse_review_decision("## Decision: Changes Requested"))

    def test_unknown_output_is_unavailable(self):
        self.assertEqual("Unavailable", parse_review_decision("Looks fine"))

    def test_review_engine_approved(self):
        command = f"{sys.executable} -c \"print('Approved')\""
        result = ReviewEngine().run(policy=self.policy(command), request=self.request())

        self.assertEqual("PASS", result.status)
        self.assertEqual("Approved", result.decision)
        self.assertEqual("abc123", result.reviewed_sha)

    def test_review_engine_changes_requested(self):
        command = f"{sys.executable} -c \"print('Changes Requested')\""
        result = ReviewEngine().run(policy=self.policy(command), request=self.request())

        self.assertEqual("PASS", result.status)
        self.assertEqual("Changes Requested", result.decision)

    def test_review_engine_blocks_on_unknown_output(self):
        command = f"{sys.executable} -c \"print('Maybe')\""
        result = ReviewEngine().run(policy=self.policy(command), request=self.request())

        self.assertEqual("BLOCK", result.status)
        self.assertEqual("REVIEW_DECISION_UNAVAILABLE", result.violations[0].code)

    def test_review_engine_blocks_on_nonzero_exit(self):
        command = f"{sys.executable} -c \"import sys; sys.exit(2)\""
        result = ReviewEngine().run(policy=self.policy(command), request=self.request())

        self.assertEqual("BLOCK", result.status)
        self.assertEqual("REVIEWER_COMMAND_FAILED", result.violations[0].code)


if __name__ == "__main__":
    unittest.main()

import unittest
from pathlib import Path

from ados.exact_head_gate import ExactHeadGate
from ados.repository_provider import RepositoryProviderError


class FakeProvider:
    def __init__(self, head="abc123", error=None):
        self.head = head
        self.error = error

    def current_head(self, path):
        if self.error:
            raise self.error
        return self.head


class ExactHeadGateTests(unittest.TestCase):
    def test_match(self):
        result = ExactHeadGate(FakeProvider("abc123")).verify(
            repository_path=Path("."),
            approved_review_sha="abc123",
            validated_sha="abc123",
        )

        self.assertEqual("MATCH", result.status)
        self.assertEqual((), result.violations)

    def test_approved_review_mismatch_blocks(self):
        result = ExactHeadGate(FakeProvider("head")).verify(
            repository_path=Path("."),
            approved_review_sha="review",
            validated_sha="head",
        )

        self.assertEqual("BLOCK", result.status)
        self.assertEqual("APPROVED_REVIEW_SHA_MISMATCH", result.violations[0].code)

    def test_validated_mismatch_blocks(self):
        result = ExactHeadGate(FakeProvider("head")).verify(
            repository_path=Path("."),
            approved_review_sha="head",
            validated_sha="validated",
        )

        self.assertEqual("BLOCK", result.status)
        self.assertEqual("VALIDATED_SHA_MISMATCH", result.violations[0].code)

    def test_repository_error_blocks(self):
        result = ExactHeadGate(FakeProvider(error=RepositoryProviderError("NOT_GIT_REPOSITORY", "not git"))).verify(
            repository_path=Path("."),
            approved_review_sha="head",
            validated_sha="head",
        )

        self.assertEqual("BLOCK", result.status)
        self.assertEqual("NOT_GIT_REPOSITORY", result.violations[0].code)


if __name__ == "__main__":
    unittest.main()

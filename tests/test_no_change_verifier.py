import unittest

from ados.no_change_verifier import parse_no_change_verification_decision


class NoChangeVerifierTests(unittest.TestCase):
    def test_plain_supported_decisions(self):
        self.assertEqual("NO_CHANGES_VERIFIED", parse_no_change_verification_decision("NO_CHANGES_VERIFIED\n"))
        self.assertEqual("FEATURE_MISSING", parse_no_change_verification_decision("FEATURE_MISSING\n"))
        self.assertEqual("AMBIGUOUS", parse_no_change_verification_decision("AMBIGUOUS\n"))

    def test_decision_section_allows_explanation_suffix(self):
        self.assertEqual("FEATURE_MISSING", parse_no_change_verification_decision("### Decision\n\n**FEATURE_MISSING** - missing runtime path\n"))

    def test_ambiguous_prose_does_not_parse_as_missing(self):
        self.assertEqual("AMBIGUOUS", parse_no_change_verification_decision("The feature may be missing, but this is not a decision line.\n"))

    def test_conflicting_decisions_are_ambiguous(self):
        self.assertEqual("AMBIGUOUS", parse_no_change_verification_decision("NO_CHANGES_VERIFIED\nFEATURE_MISSING\n"))


if __name__ == "__main__":
    unittest.main()

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

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

    def request(self, repo=None, *, candidate_sha="abc123", base_sha="base123", scope="test"):
        return ReviewRequest(repository_path=Path(repo or "."), candidate_sha=candidate_sha, base_sha=base_sha, scope=scope, diff="diff")

    def test_parse_approved(self):
        self.assertEqual("Approved", parse_review_decision("**Approved**\nReviewed SHA: abc123"))

    def test_parse_approved_with_markdown_and_whitespace(self):
        self.assertEqual("Approved", parse_review_decision("  **Approved**  "))

    def test_parse_approved_with_single_marker_markdown(self):
        self.assertEqual("Approved", parse_review_decision("*Approved*"))
        self.assertEqual("Approved", parse_review_decision("_Approved_"))

    def test_parse_changes_requested(self):
        self.assertEqual("Changes Requested", parse_review_decision("## Decision: Changes Requested"))

    def test_parse_decision_prefix_with_markdown_decision(self):
        self.assertEqual("Approved", parse_review_decision("Decision: **Approved**"))

    def test_parse_markdown_decision_label_approved(self):
        self.assertEqual("Approved", parse_review_decision("**Decision**: Approved"))

    def test_parse_markdown_decision_label_markdown_approved(self):
        self.assertEqual("Approved", parse_review_decision("**Decision**: **Approved**"))

    def test_parse_decision_prefix_with_single_marker_markdown(self):
        self.assertEqual("Approved", parse_review_decision("Decision: *Approved*"))

    def test_parse_decision_prefix_with_stray_edge_markers(self):
        self.assertEqual("Approved", parse_review_decision("Decision: Approved**"))
        self.assertEqual("Approved", parse_review_decision("**Decision: Approved"))

    def test_parse_markdown_decision_label_changes_requested(self):
        self.assertEqual("Changes Requested", parse_review_decision("**Decision**: Changes Requested"))

    def test_parse_markdown_decision_label_markdown_changes_requested(self):
        self.assertEqual("Changes Requested", parse_review_decision("**Decision**: **Changes Requested**"))

    def test_parse_decision_prefix_with_markdown_changes_requested(self):
        self.assertEqual("Changes Requested", parse_review_decision("Decision: **Changes Requested**"))

    def test_parse_review_heading_approved(self):
        self.assertEqual("Approved", parse_review_decision("## Review: Approved"))

    def test_parse_review_heading_markdown_label_approved(self):
        self.assertEqual("Approved", parse_review_decision("## **Review**: Approved"))

    def test_parse_review_heading_markdown_label_changes_requested(self):
        self.assertEqual("Changes Requested", parse_review_decision("## **Review**: Changes Requested"))

    def test_parse_review_heading_with_single_marker_markdown(self):
        self.assertEqual("Approved", parse_review_decision("## Review: *Approved*"))

    def test_parse_review_heading_markdown_approved(self):
        self.assertEqual("Approved", parse_review_decision("## Review: **Approved**"))

    def test_parse_review_prefix_approved(self):
        self.assertEqual("Approved", parse_review_decision("Review: Approved"))

    def test_parse_review_prefix_markdown_approved(self):
        self.assertEqual("Approved", parse_review_decision("Review: **Approved**"))

    def test_parse_review_heading_changes_requested(self):
        self.assertEqual("Changes Requested", parse_review_decision("## Review: Changes Requested"))

    def test_parse_review_heading_markdown_changes_requested(self):
        self.assertEqual("Changes Requested", parse_review_decision("## Review: **Changes Requested**"))

    def test_parse_plain_changes_requested(self):
        self.assertEqual("Changes Requested", parse_review_decision("Changes Requested"))

    def test_unknown_output_is_unavailable(self):
        self.assertEqual("Unavailable", parse_review_decision("Looks fine"))

    def test_prose_containing_approved_is_unavailable(self):
        self.assertEqual(
            "Unavailable",
            parse_review_decision("The previous review was Approved, but this candidate has blocking findings."),
        )

    def test_conflicting_explicit_decisions_are_unavailable(self):
        self.assertEqual(
            "Unavailable",
            parse_review_decision("## Review: Approved\nDecision: Changes Requested"),
        )

    def test_review_engine_approved(self):
        with self.project() as fixture:
            command = f'"{sys.executable}" "{self.reviewer_script(fixture.root / "reviewer.py")}"'
            result = ReviewEngine().run(policy=self.policy(command), request=self.request(fixture.repo, candidate_sha=fixture.candidate, base_sha=fixture.base, scope="scope"))

        self.assertEqual("PASS", result.status)
        self.assertEqual("Approved", result.decision)
        self.assertEqual(fixture.candidate, result.reviewed_sha)

    def test_review_engine_changes_requested(self):
        with self.project() as fixture:
            command = f'"{sys.executable}" "{self.reviewer_script(fixture.root / "reviewer.py", decision="Changes Requested")}"'
            result = ReviewEngine().run(policy=self.policy(command), request=self.request(fixture.repo, candidate_sha=fixture.candidate, base_sha=fixture.base, scope="scope"))

        self.assertEqual("PASS", result.status)
        self.assertEqual("Changes Requested", result.decision)

    def test_review_engine_blocks_on_unknown_output(self):
        with self.project() as fixture:
            command = f'"{sys.executable}" "{self.reviewer_script(fixture.root / "reviewer.py", decision="Maybe")}"'
            result = ReviewEngine().run(policy=self.policy(command), request=self.request(fixture.repo, candidate_sha=fixture.candidate, base_sha=fixture.base, scope="scope"))

        self.assertEqual("BLOCK", result.status)
        self.assertEqual("REVIEW_DECISION_UNAVAILABLE", result.violations[0].code)

    def test_review_engine_blocks_on_malformed_successful_output(self):
        with self.project() as fixture:
            command = f'"{sys.executable}" "{self.reviewer_script(fixture.root / "reviewer.py", decision="The candidate was Approved before, but not now.")}"'
            result = ReviewEngine().run(policy=self.policy(command), request=self.request(fixture.repo, candidate_sha=fixture.candidate, base_sha=fixture.base, scope="scope"))

        self.assertEqual("BLOCK", result.status)
        self.assertEqual("Unavailable", result.decision)
        self.assertEqual("REVIEW_DECISION_UNAVAILABLE", result.violations[0].code)

    def test_review_engine_accepts_markdown_decision_prefix(self):
        with self.project() as fixture:
            command = f'"{sys.executable}" "{self.reviewer_script(fixture.root / "reviewer.py", decision="Decision: **Approved**")}"'
            result = ReviewEngine().run(policy=self.policy(command), request=self.request(fixture.repo, candidate_sha=fixture.candidate, base_sha=fixture.base, scope="scope"))

        self.assertEqual("PASS", result.status)
        self.assertEqual("Approved", result.decision)

    def test_review_engine_accepts_review_heading_decision(self):
        with self.project() as fixture:
            command = f'"{sys.executable}" "{self.reviewer_script(fixture.root / "reviewer.py", decision="## Review: Approved")}"'
            result = ReviewEngine().run(policy=self.policy(command), request=self.request(fixture.repo, candidate_sha=fixture.candidate, base_sha=fixture.base, scope="scope"))

        self.assertEqual("PASS", result.status)
        self.assertEqual("Approved", result.decision)

    def test_review_engine_blocks_on_nonzero_exit(self):
        with self.project() as fixture:
            command = f'"{sys.executable}" "{self.reviewer_script(fixture.root / "reviewer.py", decision="Maybe", exit_code=2)}"'
            result = ReviewEngine().run(policy=self.policy(command), request=self.request(fixture.repo, candidate_sha=fixture.candidate, base_sha=fixture.base, scope="scope"))

        self.assertEqual("BLOCK", result.status)
        self.assertEqual("Unavailable", result.decision)
        self.assertEqual("REVIEWER_COMMAND_FAILED", result.violations[0].code)

    def test_review_runs_in_target_repo_not_launch_cwd(self):
        with self.project() as fixture, tempfile.TemporaryDirectory(prefix="ados launch cwd ") as launch_cwd:
            marker = fixture.root / "cwd.txt"
            command = f'"{sys.executable}" "{self.reviewer_script(fixture.root / "reviewer.py", marker=marker)}"'
            previous = Path.cwd()
            try:
                import os

                os.chdir(launch_cwd)
                result = ReviewEngine().run(policy=self.policy(command), request=self.request(fixture.repo, candidate_sha=fixture.candidate, base_sha=fixture.base, scope="scope"))
                cwd_text = marker.read_text(encoding="utf-8")
            finally:
                os.chdir(previous)

        self.assertEqual("PASS", result.status)
        self.assertEqual(str(fixture.repo), cwd_text)

    def test_cli_review_repo_argument_binds_external_target(self):
        with self.project() as fixture:
            command = f'"{sys.executable}" "{self.reviewer_script(fixture.root / "reviewer.py")}"'
            policy = fixture.root / "policy.json"
            self.write_policy(policy, command)
            completed = subprocess.run(
                (
                    sys.executable,
                    "-m",
                    "ados",
                    "review",
                    "run",
                    "--policy",
                    str(policy),
                    "--repo",
                    str(fixture.repo),
                    "--candidate-sha",
                    fixture.candidate,
                    "--base-sha",
                    fixture.base,
                    "--scope",
                    "scope",
                ),
                cwd=fixture.root / "other cwd",
                capture_output=True,
                text=True,
                env={**os.environ, "PYTHONPATH": str(Path.cwd())},
            )

        self.assertEqual(0, completed.returncode, completed.stdout + completed.stderr)
        self.assertIn("Approved", completed.stdout)

    def test_missing_repo_candidate_base_and_scope_block_before_spawn(self):
        with self.project() as fixture:
            command = f'"{sys.executable}" "{self.reviewer_script(fixture.root / "reviewer.py")}"'
            missing_repo = ReviewEngine().run(policy=self.policy(command), request=self.request(fixture.root / "missing", candidate_sha=fixture.candidate, base_sha=fixture.base, scope="scope"))
            missing_candidate = ReviewEngine().run(policy=self.policy(command), request=self.request(fixture.repo, candidate_sha="f" * 40, base_sha=fixture.base, scope="scope"))
            missing_base = ReviewEngine().run(policy=self.policy(command), request=self.request(fixture.repo, candidate_sha=fixture.candidate, base_sha="e" * 40, scope="scope"))
            missing_scope = ReviewEngine().run(policy=self.policy(command), request=self.request(fixture.repo, candidate_sha=fixture.candidate, base_sha=fixture.base, scope="specs/missing-scope"))

        self.assertEqual("REVIEW_REPOSITORY_MISSING", missing_repo.violations[0].code)
        self.assertEqual("REVIEW_CANDIDATE_MISSING", missing_candidate.violations[0].code)
        self.assertEqual("REVIEW_BASE_MISSING", missing_base.violations[0].code)
        self.assertEqual("REVIEW_SCOPE_MISSING", missing_scope.violations[0].code)

    def test_free_text_scope_does_not_require_repository_path(self):
        with self.project() as fixture:
            command = f'"{sys.executable}" "{self.reviewer_script(fixture.root / "reviewer.py")}"'
            result = ReviewEngine().run(policy=self.policy(command), request=self.request(fixture.repo, candidate_sha=fixture.candidate, base_sha=fixture.base, scope="Spec005"))

        self.assertEqual("Approved", result.decision)

    def test_review_invocation_uses_no_shell_and_target_cwd(self):
        with self.project() as fixture:
            with mock.patch("ados.review_engine.subprocess.run") as mocked:
                def fake_run(args, **kwargs):
                    if args[0] == "git":
                        return subprocess.CompletedProcess(args, 0, "ok", "")
                    return subprocess.CompletedProcess(args, 0, "Approved", "")

                mocked.side_effect = fake_run
                result = ReviewEngine().run(policy=self.policy("reviewer --flag"), request=self.request(fixture.repo, candidate_sha=fixture.candidate, base_sha=fixture.base, scope="scope"))

        reviewer_call = [call for call in mocked.call_args_list if call.args[0][0] != "git"][0]
        self.assertEqual("PASS", result.status)
        self.assertIs(reviewer_call.kwargs["shell"], False)
        self.assertEqual("utf-8", reviewer_call.kwargs["encoding"])
        self.assertEqual("replace", reviewer_call.kwargs["errors"])
        self.assertEqual(fixture.repo.resolve(), reviewer_call.kwargs["cwd"])

    def test_unsafe_reviewer_command_blocks_before_spawn(self):
        with self.project() as fixture:
            result = ReviewEngine().run(policy=self.policy("reviewer && bad"), request=self.request(fixture.repo, candidate_sha=fixture.candidate, base_sha=fixture.base, scope="scope"))

        self.assertEqual("BLOCK", result.status)
        self.assertEqual("REVIEWER_COMMAND_UNSAFE", result.violations[0].code)

    def project(self):
        return TemporaryReviewProject(self)

    def reviewer_script(self, path, *, decision="Approved", exit_code=0, marker=None):
        lines = ["from pathlib import Path", "import os", "import sys"]
        if marker is not None:
            lines.append(f"Path(r'{marker}').write_text(os.getcwd(), encoding='utf-8')")
        lines.append(f"print({decision!r})")
        lines.append(f"sys.exit({exit_code})")
        path.write_text("\n".join(lines), encoding="utf-8")
        return path

    def write_policy(self, path, reviewer_command):
        path.write_text(
            json.dumps(
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
            ),
            encoding="utf-8",
        )


class TemporaryReviewProject:
    def __init__(self, test):
        self.test = test

    def __enter__(self):
        self.temp = tempfile.TemporaryDirectory(prefix="ados review path ")
        self.root = Path(self.temp.name)
        self.repo = self.root / "target repo"
        self.repo.mkdir()
        self.test.git(self.repo, "init", "-b", "main")
        self.test.git(self.repo, "config", "user.email", "test@example.invalid")
        self.test.git(self.repo, "config", "user.name", "Test User")
        (self.repo / "README.md").write_text("base\n", encoding="utf-8")
        (self.repo / "scope").mkdir()
        (self.repo / "scope" / "spec.md").write_text("base\n", encoding="utf-8")
        self.test.git(self.repo, "add", ".")
        self.test.git(self.repo, "commit", "-m", "base")
        self.base = self.test.head(self.repo)
        (self.repo / "scope" / "spec.md").write_text("candidate\n", encoding="utf-8")
        self.test.git(self.repo, "add", ".")
        self.test.git(self.repo, "commit", "-m", "candidate")
        self.candidate = self.test.head(self.repo)
        (self.root / "other cwd").mkdir()
        return self

    def __exit__(self, exc_type, exc, traceback):
        self.temp.cleanup()


def _git(repo, *args):
    return subprocess.run(("git", *args), cwd=repo, check=True, capture_output=True, text=True)


ReviewEngineTests.git = staticmethod(_git)
ReviewEngineTests.head = staticmethod(lambda repo: _git(repo, "rev-parse", "HEAD").stdout.strip())


if __name__ == "__main__":
    unittest.main()

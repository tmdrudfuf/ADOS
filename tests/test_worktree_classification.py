from pathlib import Path
import tempfile
import unittest

from ados.repository_provider import RepositoryStatus
from ados.worktree_classification import classify_worktree
from ados.worktree_provider import WorktreeRecord


class WorktreeClassificationTests(unittest.TestCase):
    def test_spec_branch_uses_merged_pull_request_head_evidence(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            primary = root / "project"
            worktree = root / "project-spec-077"
            primary.mkdir()
            worktree.mkdir()
            git = FakeGit({("spec-head", "merged-pr-head"): True})

            result = classify_worktree(
                record=WorktreeRecord(worktree, "codex/077-review-decision", "spec-head"),
                primary_root=primary,
                current_main_head="main-head",
                latest_merged_spec=83,
                merged_pull_request_heads=("merged-pr-head",),
                git=git,
            )

        self.assertEqual("MERGED_HISTORICAL", result.classification)
        self.assertEqual("head_reachable_from_merged_pull_request_head", result.evidence["merged_evidence"])

    def test_lower_numbered_spec_without_merge_evidence_remains_active(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            primary = root / "project"
            worktree = root / "project-spec-002"
            primary.mkdir()
            worktree.mkdir()
            git = FakeGit({})

            result = classify_worktree(
                record=WorktreeRecord(worktree, "codex/002-abandoned", "abandoned-head"),
                primary_root=primary,
                current_main_head="main-head",
                latest_merged_spec=3,
                merged_spec_numbers=frozenset({3}),
                git=git,
            )

        self.assertEqual("ACTIVE", result.classification)
        self.assertIn("UNMERGED_SPEC_WORKTREE", result.reason_codes)


class FakeGit:
    def __init__(self, ancestry):
        self.ancestry = ancestry

    def status(self, path):
        return RepositoryStatus(
            root=path,
            branch="branch",
            head="head",
            staged=(),
            dirty_tracked=(),
            untracked=(),
        )

    def is_ancestor(self, path, ancestor, descendant):
        return self.ancestry.get((ancestor, descendant), False)


if __name__ == "__main__":
    unittest.main()

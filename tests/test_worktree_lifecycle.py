import unittest
from pathlib import Path

from ados.execution_policy import ExecutionPolicy
from ados.primary_repository_guardian import GuardianResult, GuardianViolation
from ados.worktree_lifecycle import WorktreeLifecycleEngine, WorktreeRequest
from ados.worktree_provider import WorktreeRecord


class FakeGuardian:
    def __init__(self, result):
        self.result = result
        self.calls = 0

    def audit(self, **kwargs):
        self.calls += 1
        return self.result


class FakeWorktreeProvider:
    def __init__(self, records=()):
        self.records = list(records)
        self.created = []
        self.removed = []

    def list_worktrees(self, primary_repository_path):
        return tuple(self.records)

    def create_worktree(self, primary_repository_path, worktree_path, branch, base_ref):
        self.created.append((primary_repository_path, worktree_path, branch, base_ref))
        self.records.append(WorktreeRecord(path=worktree_path.resolve(), branch=branch, head="created"))

    def remove_worktree(self, primary_repository_path, worktree_path):
        self.removed.append((primary_repository_path, worktree_path))


class WorktreeLifecycleTests(unittest.TestCase):
    def policy(self, autonomous=True):
        return ExecutionPolicy.from_mapping(
            {
                "execution_policy": {
                    "schema_version": "1",
                    "publication": {"merge_strategy": "merge"},
                    "review": {"reviewer": "reviewer", "max_rounds": 5},
                    "cleanup": {"autonomous": autonomous},
                    "guardian": {"stop_on_uncertain": True},
                    "validation": {"commands": ["git diff --check"]},
                }
            }
        )

    def request(self, **overrides):
        values = {
            "primary_repository_path": Path("/repo"),
            "worktree_path": Path("/repo-wt"),
            "branch": "feature/example",
            "base_ref": "main",
        }
        values.update(overrides)
        return WorktreeRequest(**values)

    def guardian_pass(self):
        return GuardianResult("primary_repository", "PASS", (), ())

    def test_create_is_gated_by_primary_guardian(self):
        guardian = FakeGuardian(
            GuardianResult(
                "primary_repository",
                "BLOCK",
                (GuardianViolation("DIRTY_TRACKED_FILES", "dirty", {"files": "x"}),),
                (),
            )
        )
        provider = FakeWorktreeProvider()
        result = WorktreeLifecycleEngine(guardian=guardian, provider=provider).create(
            policy=self.policy(),
            request=self.request(),
        )

        self.assertEqual("BLOCK", result.status)
        self.assertEqual("PRIMARY_DIRTY_TRACKED_FILES", result.violations[0].code)
        self.assertEqual([], provider.created)

    def test_create_adds_and_verifies_worktree(self):
        provider = FakeWorktreeProvider()
        result = WorktreeLifecycleEngine(guardian=FakeGuardian(self.guardian_pass()), provider=provider).create(
            policy=self.policy(),
            request=self.request(),
        )

        self.assertEqual("PASS", result.status)
        self.assertEqual(1, len(provider.created))

    def test_verify_blocks_unregistered_worktree(self):
        result = WorktreeLifecycleEngine(provider=FakeWorktreeProvider()).verify(
            policy=self.policy(),
            request=self.request(),
        )

        self.assertEqual("BLOCK", result.status)
        self.assertEqual("WORKTREE_NOT_REGISTERED", result.violations[0].code)

    def test_verify_detects_branch_mismatch(self):
        provider = FakeWorktreeProvider((WorktreeRecord(Path("/repo-wt").resolve(), "other", "abc"),))
        result = WorktreeLifecycleEngine(provider=provider).verify(policy=self.policy(), request=self.request())

        self.assertEqual("BLOCK", result.status)
        self.assertEqual("WORKTREE_BRANCH_MISMATCH", result.violations[0].code)

    def test_remove_requires_cleanup_autonomy(self):
        provider = FakeWorktreeProvider((WorktreeRecord(Path("/repo-wt").resolve(), "feature/example", "abc"),))
        result = WorktreeLifecycleEngine(provider=provider).remove(
            policy=self.policy(autonomous=False),
            request=self.request(),
        )

        self.assertEqual("BLOCK", result.status)
        self.assertEqual("CLEANUP_AUTONOMY_DISABLED", result.violations[0].code)
        self.assertEqual([], provider.removed)

    def test_remove_targets_only_explicit_worktree(self):
        provider = FakeWorktreeProvider((WorktreeRecord(Path("/repo-wt").resolve(), "feature/example", "abc"),))
        result = WorktreeLifecycleEngine(provider=provider).remove(policy=self.policy(), request=self.request())

        self.assertEqual("PASS", result.status)
        self.assertEqual([(Path("/repo"), Path("/repo-wt"))], provider.removed)

    def test_blocks_worktree_equal_to_primary(self):
        result = WorktreeLifecycleEngine(provider=FakeWorktreeProvider()).verify(
            policy=self.policy(),
            request=self.request(worktree_path=Path("/repo")),
        )

        self.assertEqual("BLOCK", result.status)
        self.assertEqual("WORKTREE_EQUALS_PRIMARY", result.violations[0].code)


if __name__ == "__main__":
    unittest.main()

import subprocess
import tempfile
import unittest
from pathlib import Path

from ados.worktree_provider import GitWorktreeProvider


class GitWorktreeProviderTests(unittest.TestCase):
    def test_real_git_worktree_create_list_and_remove(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            repo = root / "repo"
            worktree = root / "feature-worktree"
            repo.mkdir()
            self.git(repo, "init")
            self.git(repo, "config", "user.email", "test@example.invalid")
            self.git(repo, "config", "user.name", "Test User")
            (repo / "README.md").write_text("base\n", encoding="utf-8")
            self.git(repo, "add", "README.md")
            self.git(repo, "commit", "-m", "initial")

            provider = GitWorktreeProvider()
            provider.create_worktree(repo, worktree, "feature/example", "HEAD")

            records = provider.list_worktrees(repo)
            matching = [record for record in records if record.path == worktree.resolve()]
            self.assertEqual(1, len(matching))
            self.assertEqual("feature/example", matching[0].branch)
            self.assertTrue(matching[0].head)

            provider.remove_worktree(repo, worktree)
            records_after_remove = provider.list_worktrees(repo)
            self.assertNotIn(worktree.resolve(), {record.path for record in records_after_remove})

    def git(self, repo, *args):
        return subprocess.run(("git", *args), cwd=repo, check=True, capture_output=True, text=True)


if __name__ == "__main__":
    unittest.main()

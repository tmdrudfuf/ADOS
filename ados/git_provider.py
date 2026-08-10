"""Read-only Git repository provider."""

from __future__ import annotations

from pathlib import Path
import json
import subprocess

from .repository_provider import RepositoryProviderError, RepositoryStatus


class GitRepositoryProvider:
    """Repository provider backed by read-only Git commands."""

    def repository_root(self, path: Path) -> Path:
        return Path(self._git(path, "rev-parse", "--show-toplevel")).resolve()

    def current_branch(self, path: Path) -> str:
        return self._git(path, "branch", "--show-current")

    def current_head(self, path: Path) -> str:
        return self._git(path, "rev-parse", "HEAD")

    def ref_head(self, path: Path, ref: str) -> str:
        return self._git(path, "rev-parse", ref)

    def branch_exists(self, path: Path, branch: str) -> bool:
        try:
            self._git(path, "rev-parse", "--verify", f"refs/heads/{branch}")
        except RepositoryProviderError:
            return False
        return True

    def local_branches(self, path: Path) -> tuple[str, ...]:
        output = self._git(path, "for-each-ref", "--format=%(refname:short)", "refs/heads")
        return tuple(line for line in output.splitlines() if line.strip())

    def origin_url(self, path: Path) -> str:
        return self._git(path, "remote", "get-url", "origin")

    def merged_pull_request_heads(self, path: Path, current_head: str) -> tuple[str, ...]:
        try:
            origin = self.origin_url(path)
        except RepositoryProviderError:
            return ()
        if "github.com" not in origin.lower():
            return ()
        try:
            completed = subprocess.run(
                (
                    "gh",
                    "pr",
                    "list",
                    "--state",
                    "merged",
                    "--json",
                    "headRefOid,mergeCommit",
                    "--limit",
                    "200",
                ),
                cwd=path,
                check=False,
                capture_output=True,
                text=True,
            )
        except (FileNotFoundError, OSError):
            return ()
        if completed.returncode != 0:
            return ()
        try:
            raw = json.loads(completed.stdout)
        except json.JSONDecodeError:
            return ()
        heads: list[str] = []
        if not isinstance(raw, list):
            return ()
        for item in raw:
            if not isinstance(item, dict):
                continue
            head = str(item.get("headRefOid", ""))
            merge = item.get("mergeCommit", {})
            merge_oid = str(merge.get("oid", "")) if isinstance(merge, dict) else ""
            if not head or not merge_oid:
                continue
            try:
                if self.is_ancestor(path, merge_oid, current_head):
                    heads.append(head)
            except RepositoryProviderError:
                continue
        return tuple(heads)

    def is_ancestor(self, path: Path, ancestor: str, descendant: str) -> bool:
        if not path.is_dir():
            raise RepositoryProviderError("REPOSITORY_PATH_INVALID", f"repository path is not a directory: {path}")
        try:
            completed = subprocess.run(
                ("git", "merge-base", "--is-ancestor", ancestor, descendant),
                cwd=path,
                check=False,
                capture_output=True,
                text=True,
            )
        except FileNotFoundError as exc:
            raise RepositoryProviderError("GIT_UNAVAILABLE", "git executable is unavailable") from exc
        except OSError as exc:
            raise RepositoryProviderError("REPOSITORY_PATH_INVALID", str(exc)) from exc
        if completed.returncode == 0:
            return True
        if completed.returncode == 1:
            return False
        message = (completed.stderr or completed.stdout or "git merge-base failed").strip()
        raise RepositoryProviderError("GIT_ANCESTRY_UNAVAILABLE", message)

    def status(self, path: Path) -> RepositoryStatus:
        root = self.repository_root(path)
        branch = self.current_branch(root)
        head = self.current_head(root)
        staged: list[str] = []
        dirty_tracked: list[str] = []
        untracked: list[str] = []

        output = self._git(root, "status", "--porcelain=v1", "--untracked-files=all", strip_output=False)
        for line in output.splitlines():
            if not line:
                continue
            status_code = line[:2]
            file_path = line[3:]
            if status_code == "??":
                untracked.append(file_path)
                continue
            if status_code[0] != " ":
                staged.append(file_path)
            if status_code[1] != " ":
                dirty_tracked.append(file_path)

        return RepositoryStatus(
            root=root,
            branch=branch,
            head=head,
            staged=tuple(staged),
            dirty_tracked=tuple(dirty_tracked),
            untracked=tuple(untracked),
        )

    def _git(self, path: Path, *args: str, strip_output: bool = True) -> str:
        if not path.is_dir():
            raise RepositoryProviderError("REPOSITORY_PATH_INVALID", f"repository path is not a directory: {path}")
        try:
            completed = subprocess.run(
                ("git", *args),
                cwd=path,
                check=True,
                capture_output=True,
                text=True,
            )
        except FileNotFoundError as exc:
            raise RepositoryProviderError("GIT_UNAVAILABLE", "git executable is unavailable") from exc
        except OSError as exc:
            raise RepositoryProviderError("REPOSITORY_PATH_INVALID", str(exc)) from exc
        except subprocess.CalledProcessError as exc:
            message = (exc.stderr or exc.stdout or str(exc)).strip()
            raise RepositoryProviderError("NOT_GIT_REPOSITORY", message) from exc
        if strip_output:
            return completed.stdout.strip()
        return completed.stdout.rstrip("\n")

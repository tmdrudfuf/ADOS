"""Read-only Git repository provider."""

from __future__ import annotations

from pathlib import Path
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

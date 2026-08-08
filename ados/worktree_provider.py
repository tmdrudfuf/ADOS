"""Git worktree provider."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import subprocess

from .repository_provider import RepositoryProviderError


@dataclass(frozen=True)
class WorktreeRecord:
    path: Path
    branch: str
    head: str


class GitWorktreeProvider:
    def list_worktrees(self, primary_repository_path: Path) -> tuple[WorktreeRecord, ...]:
        output = self._git(primary_repository_path, "worktree", "list", "--porcelain")
        records: list[WorktreeRecord] = []
        current: dict[str, str] = {}
        for line in output.splitlines():
            if not line:
                if current:
                    records.append(_record_from(current))
                    current = {}
                continue
            key, _, value = line.partition(" ")
            current[key] = value
        if current:
            records.append(_record_from(current))
        return tuple(records)

    def create_worktree(self, primary_repository_path: Path, worktree_path: Path, branch: str, base_ref: str) -> None:
        self._git(primary_repository_path, "worktree", "add", "-b", branch, str(worktree_path), base_ref)

    def remove_worktree(self, primary_repository_path: Path, worktree_path: Path) -> None:
        self._git(primary_repository_path, "worktree", "remove", str(worktree_path))

    def _git(self, path: Path, *args: str) -> str:
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
            raise RepositoryProviderError("WORKTREE_GIT_FAILED", message) from exc
        return completed.stdout.strip()


def _record_from(raw: dict[str, str]) -> WorktreeRecord:
    return WorktreeRecord(
        path=Path(raw.get("worktree", "")).resolve(),
        branch=raw.get("branch", "").removeprefix("refs/heads/"),
        head=raw.get("HEAD", ""),
    )

"""Read-only repository provider contracts."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol


@dataclass(frozen=True)
class RepositoryStatus:
    root: Path
    branch: str
    head: str
    staged: tuple[str, ...]
    dirty_tracked: tuple[str, ...]
    untracked: tuple[str, ...]


class RepositoryProvider(Protocol):
    def repository_root(self, path: Path) -> Path:
        """Return the repository root for path, or raise RepositoryProviderError."""

    def current_branch(self, path: Path) -> str:
        """Return the current branch name."""

    def current_head(self, path: Path) -> str:
        """Return the current HEAD SHA."""

    def ref_head(self, path: Path, ref: str) -> str:
        """Return the SHA for a ref."""

    def branch_exists(self, path: Path, branch: str) -> bool:
        """Return whether a local branch exists."""

    def local_branches(self, path: Path) -> tuple[str, ...]:
        """Return local branch names."""

    def is_ancestor(self, path: Path, ancestor: str, descendant: str) -> bool:
        """Return whether ancestor is reachable from descendant."""

    def changed_files(self, path: Path, base: str, head: str) -> tuple[str, ...]:
        """Return files changed between two committed revisions."""

    def status(self, path: Path) -> RepositoryStatus:
        """Return read-only repository status."""


class RepositoryProviderError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message

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

    def is_ancestor(self, path: Path, ancestor: str, descendant: str) -> bool:
        """Return whether ancestor is reachable from descendant."""

    def status(self, path: Path) -> RepositoryStatus:
        """Return read-only repository status."""


class RepositoryProviderError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message

"""Read-only worktree classification for status and run eligibility."""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import re

from .git_provider import GitRepositoryProvider
from .repository_provider import RepositoryProviderError
from .worktree_provider import WorktreeRecord


ACTIVE_RUN_STATES = {
    "READY_FOR_IMPLEMENTATION",
    "IMPLEMENTATION_FAILED",
    "IMPLEMENTATION_TIMED_OUT",
    "READY_FOR_VALIDATION",
    "VALIDATION_FAILED",
    "VALIDATION_TIMED_OUT",
    "READY_FOR_REVIEW",
    "REVIEW_CHANGES_REQUESTED",
    "BLOCKED",
}

TERMINAL_RUN_STATES = {"MERGED", "COMPLETE", "COMPLETED", "CLEANED_UP"}


@dataclass(frozen=True)
class WorktreeClassification:
    classification: str
    reason_codes: tuple[str, ...]
    evidence: dict[str, str]

    @property
    def blocks_new_run(self) -> bool:
        return self.classification in {"ACTIVE", "PRESERVED", "UNKNOWN"}


def classify_worktree(
    *,
    record: WorktreeRecord,
    primary_root: Path,
    current_main_head: str,
    latest_merged_spec: int | None,
    git: GitRepositoryProvider,
) -> WorktreeClassification:
    if record.path == primary_root:
        return WorktreeClassification("PRIMARY", (), {"path": str(record.path)})

    evidence = {
        "path": str(record.path),
        "branch": record.branch,
        "head": record.head,
    }

    if not record.path.exists():
        return WorktreeClassification("UNKNOWN", ("WORKTREE_PATH_MISSING",), evidence)

    run = _active_run(record.path)

    try:
        status = git.status(record.path)
    except RepositoryProviderError as exc:
        evidence["error"] = exc.message
        return WorktreeClassification("UNKNOWN", (exc.code,), evidence)

    dirty_reasons: list[str] = []
    if status.staged:
        dirty_reasons.append("STAGED_FILES")
        evidence["staged"] = ",".join(status.staged)
    if status.dirty_tracked:
        dirty_reasons.append("DIRTY_TRACKED_FILES")
        evidence["dirty_tracked"] = ",".join(status.dirty_tracked)
    if status.untracked:
        dirty_reasons.append("UNTRACKED_FILES")
        evidence["untracked"] = ",".join(status.untracked)
    if run is not None:
        evidence.update(run)
        return WorktreeClassification("ACTIVE", tuple(dict.fromkeys(("ACTIVE_DURABLE_RUN", *dirty_reasons))), evidence)
    if dirty_reasons:
        return WorktreeClassification("UNKNOWN", tuple(dirty_reasons), evidence)

    spec_number = branch_spec_number(record.branch)
    if spec_number is not None and latest_merged_spec is not None and spec_number <= latest_merged_spec:
        evidence["merged_evidence"] = "branch_spec_not_newer_than_latest_merged_spec"
        evidence["branch_spec"] = f"{spec_number:03d}"
        evidence["latest_merged_spec"] = f"{latest_merged_spec:03d}"
        return WorktreeClassification("MERGED_HISTORICAL", (), evidence)

    if spec_number is not None:
        evidence["branch_spec"] = f"{spec_number:03d}"
        return WorktreeClassification("ACTIVE", ("UNMERGED_SPEC_WORKTREE",), evidence)

    try:
        if git.is_ancestor(primary_root, record.head, current_main_head):
            evidence["merged_evidence"] = "head_ancestor_of_current_main"
            return WorktreeClassification("MERGED_HISTORICAL", (), evidence)
    except RepositoryProviderError as exc:
        evidence["ancestor_error"] = exc.message

    if spec_number is None:
        return WorktreeClassification("PRESERVED", ("UNMERGED_NON_SPEC_WORKTREE",), evidence)


def branch_spec_number(branch: str) -> int | None:
    match = re.search(r"(?:^|/)(\d{3})-", branch)
    return int(match.group(1)) if match else None


def _active_run(worktree_path: Path) -> dict[str, str] | None:
    runs = worktree_path / ".agent-workflow" / "runs"
    if not runs.is_dir():
        return None
    for candidate in sorted(runs.glob("*/ados-run.json"), key=lambda path: str(path)):
        try:
            raw = json.loads(candidate.read_text(encoding="utf-8-sig"))
        except (OSError, json.JSONDecodeError):
            return {
                "run_record": str(candidate),
                "run_status": "Unreadable",
            }
        if not isinstance(raw, dict):
            continue
        status = str(raw.get("status", ""))
        if status in TERMINAL_RUN_STATES:
            continue
        if status in ACTIVE_RUN_STATES or status:
            return {
                "run_id": str(raw.get("runId", "")),
                "run_status": status,
                "run_spec": str(raw.get("specNumber", "")),
                "run_record": str(candidate),
            }
    return None

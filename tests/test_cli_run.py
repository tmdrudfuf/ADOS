import contextlib
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

import ados.run_pipeline as run_pipeline
from ados.cli import main
from ados.run_command import RunRequest, RunService
from ados.run_pipeline import PipelineViolation, PullRequestInfo, RunPipeline
from ados.project_config import load_project_config
from ados.status import StatusRequest, StatusService
from ados.worktree_lifecycle import WorktreeLifecycleResult, WorktreeViolation


class CliRunTests(unittest.TestCase):
    def test_help_argument_parsing(self):
        completed = subprocess.run((sys.executable, "-m", "ados", "run", "--help"), cwd=Path.cwd(), capture_output=True, text=True)

        self.assertEqual(0, completed.returncode)
        self.assertIn("--feature", completed.stdout)

    def test_valid_run_start(self):
        with self.project(specs=[1, 2]) as fixture:
            code, stdout = self.run_cli("run", "--project", str(fixture.repo), "--config", str(fixture.config), "--feature", "Add run command")
            payload = self.read_run_record(fixture.root / "project-add-run-command", "003-add-run-command")

        self.assertEqual(0, code)
        self.assertIn("READY_FOR_PUBLICATION", stdout)
        self.assertEqual("003", payload["specNumber"])
        self.assertEqual("READY_FOR_PUBLICATION", payload["status"])
        self.assertEqual("codex/003-add-run-command", payload["featureBranch"])

    def test_explicit_spec(self):
        with self.project(specs=[1]) as fixture:
            result = RunService().run(RunRequest(fixture.repo, "Build status handoff", 7, fixture.config))

        self.assertEqual("READY_FOR_PUBLICATION", result.status)
        self.assertEqual("007", result.plan.spec_number)

    def test_automatic_next_spec(self):
        with self.project(specs=[1, 3]) as fixture:
            result = RunService().run(RunRequest(fixture.repo, "Fill gap", None, fixture.config, dry_run=True))

        self.assertEqual("PLANNED", result.status)
        self.assertEqual("002", result.plan.spec_number)

    def test_used_spec_blocks(self):
        with self.project(specs=[4]) as fixture:
            result = RunService().run(RunRequest(fixture.repo, "Duplicate", 4, fixture.config, dry_run=True))

        self.assertEqual("BLOCKED", result.status)
        self.assertIn("SPEC_NUMBER_USED", self.codes(result))

    def test_ambiguous_conflicting_worktree_blocks(self):
        with self.project() as fixture:
            worktree = fixture.root / "conflict"
            self.git(fixture.repo, "worktree", "add", "-b", "codex/005-existing", str(worktree), "HEAD")
            result = RunService().run(RunRequest(fixture.repo, "New thing", 5, fixture.config, dry_run=True))
            self.git(fixture.repo, "worktree", "remove", str(worktree))

        self.assertEqual("BLOCKED", result.status)
        self.assertIn("CONFLICTING_WORKTREE", self.codes(result))

    def test_dirty_primary_blocks_before_mutation(self):
        with self.project() as fixture:
            (fixture.repo / "README.md").write_text("dirty\n", encoding="utf-8")
            before = self.snapshot(fixture.repo)
            result = RunService().run(RunRequest(fixture.repo, "Dirty start", None, fixture.config))
            after = self.snapshot(fixture.repo)

        self.assertEqual("BLOCKED", result.status)
        self.assertIn("DIRTY_TRACKED_FILES", self.codes(result))
        self.assertEqual(before, after)

    def test_invalid_config_blocks(self):
        with self.project() as fixture:
            fixture.config.write_text("{ bad json", encoding="utf-8")
            result = RunService().run(RunRequest(fixture.repo, "Invalid", None, fixture.config, dry_run=True))

        self.assertEqual("INVALID", result.status)
        self.assertIn("PROJECT_CONFIG_INVALID_JSON", self.codes(result))

    def test_out_of_sync_base_blocks(self):
        with self.project() as fixture:
            self.git(fixture.repo, "commit", "--allow-empty", "-m", "local only")
            result = RunService().run(RunRequest(fixture.repo, "Out of sync", None, fixture.config, dry_run=True))

        self.assertEqual("BLOCKED", result.status)
        self.assertIn("DEFAULT_BRANCH_NOT_SYNCED", self.codes(result))

    def test_unrelated_safe_worktree_does_not_block(self):
        with self.project() as fixture:
            worktree = fixture.root / "other"
            self.git(fixture.repo, "worktree", "add", "-b", "codex/099-other", str(worktree), "HEAD")
            result = RunService().run(RunRequest(fixture.repo, "Fresh start", 5, fixture.config, dry_run=True))
            self.git(fixture.repo, "worktree", "remove", str(worktree))

        self.assertEqual("BLOCKED", result.status)
        self.assertIn("ACTIVE_WORKTREE_PRESENT", self.codes(result))

    def test_status_human_intervention_blocks_before_mutation(self):
        with self.project() as fixture:
            first = fixture.root / "first"
            second = fixture.root / "second"
            self.git(fixture.repo, "worktree", "add", "-b", "codex/099-first", str(first), "HEAD")
            self.git(fixture.repo, "worktree", "add", "-b", "codex/098-second", str(second), "HEAD")
            before = self.snapshot(fixture.repo)
            result = RunService().run(RunRequest(fixture.repo, "Must not start", 5, fixture.config))
            after = self.snapshot(fixture.repo)
            self.git(fixture.repo, "worktree", "remove", str(first))
            self.git(fixture.repo, "worktree", "remove", str(second))

        self.assertEqual("BLOCKED", result.status)
        self.assertIn("UNSAFE_RECOVERY_STATE", self.codes(result))
        self.assertEqual(before, after)
        self.assertEqual(before, after)

    def test_multiple_historical_merged_worktrees_do_not_block_new_run(self):
        with self.project(specs=[1, 2, 3]) as fixture:
            self.write_archive(fixture.repo, spec="001-example", merge_commit=self.head(fixture.repo))
            self.write_archive(fixture.repo, spec="002-example", merge_commit=self.head(fixture.repo))
            self.write_archive(fixture.repo, spec="003-example", merge_commit=self.head(fixture.repo))
            first = fixture.root / "old-001"
            second = fixture.root / "old-002"
            self.git(fixture.repo, "worktree", "add", "-b", "codex/001-old", str(first), "HEAD")
            self.git(fixture.repo, "worktree", "add", "-b", "codex/002-old", str(second), "HEAD")
            before = self.snapshot(fixture.repo)
            result = RunService().run(RunRequest(fixture.repo, "New production fix", 4, fixture.config, dry_run=True))
            after = self.snapshot(fixture.repo)
            self.git(fixture.repo, "worktree", "remove", str(first))
            self.git(fixture.repo, "worktree", "remove", str(second))

        self.assertEqual("PLANNED", result.status)
        self.assertEqual(before, after)
        self.assertTrue(any(warning.code == "HISTORICAL_WORKTREES_PRESENT" for warning in result.eligibility.warnings))

    def test_unknown_dirty_historical_worktree_blocks_run_start(self):
        with self.project(specs=[1]) as fixture:
            self.write_archive(fixture.repo, spec="001-example", merge_commit=self.head(fixture.repo))
            worktree = fixture.root / "dirty-historical"
            self.git(fixture.repo, "worktree", "add", "-b", "codex/001-old", str(worktree), "HEAD")
            (worktree / "scratch.txt").write_text("unique\n", encoding="utf-8")
            before = self.snapshot(fixture.repo)
            result = RunService().run(RunRequest(fixture.repo, "Blocked by dirty old tree", 2, fixture.config, dry_run=True))
            after = self.snapshot(fixture.repo)
            (worktree / "scratch.txt").unlink()
            self.git(fixture.repo, "worktree", "remove", str(worktree))

        self.assertEqual("BLOCKED", result.status)
        self.assertIn("UNSAFE_RECOVERY_STATE", self.codes(result))

    def test_abandoned_lower_numbered_spec_branch_blocks_run_start(self):
        with self.project(specs=[1, 2, 3]) as fixture:
            self.write_archive(fixture.repo, spec="003-example", merge_commit=self.head(fixture.repo))
            worktree = fixture.root / "abandoned-002"
            self.git(fixture.repo, "worktree", "add", "-b", "codex/002-abandoned", str(worktree), "HEAD")
            (worktree / "abandoned.txt").write_text("abandoned\n", encoding="utf-8")
            self.git(worktree, "add", "abandoned.txt")
            self.git(worktree, "commit", "-m", "abandoned spec work")
            before = self.snapshot(fixture.repo)
            result = RunService().run(RunRequest(fixture.repo, "New work", 4, fixture.config, dry_run=True))
            after = self.snapshot(fixture.repo)
            self.git(fixture.repo, "worktree", "remove", str(worktree))

        self.assertEqual("BLOCKED", result.status)
        self.assertIn("ACTIVE_WORKTREE_PRESENT", self.codes(result))
        self.assertEqual(before, after)

    def test_preserved_worktree_blocks_run_start(self):
        with self.project() as fixture:
            worktree = fixture.root / "preserved"
            self.git(fixture.repo, "worktree", "add", "-b", "experiment-preserve", str(worktree), "HEAD")
            (worktree / "preserved.txt").write_text("preserve\n", encoding="utf-8")
            self.git(worktree, "add", "preserved.txt")
            self.git(worktree, "commit", "-m", "preserved branch work")
            result = RunService().run(RunRequest(fixture.repo, "Blocked by preserved", 2, fixture.config, dry_run=True))
            self.git(fixture.repo, "worktree", "remove", str(worktree))

        self.assertEqual("BLOCKED", result.status)
        self.assertIn("UNSAFE_RECOVERY_STATE", self.codes(result))

    def test_stale_historical_evidence_warns_not_blocks(self):
        with self.project() as fixture:
            self.write_archive(fixture.repo, validated_sha="1" * 40, approved_review_sha="1" * 40)
            result = RunService().run(RunRequest(fixture.repo, "Ignore old evidence", None, fixture.config, dry_run=True))

        self.assertEqual("PLANNED", result.status)
        self.assertTrue(result.eligibility.warnings)

    def test_changes_requested_review_state_does_not_claim_recovery_block(self):
        with self.project() as fixture:
            self.write_archive(fixture.repo, approved_review_sha=self.head(fixture.repo), decision="Changes Requested")
            result = RunService().run(RunRequest(fixture.repo, "New unrelated spec", None, fixture.config, dry_run=True))

        self.assertEqual("PLANNED", result.status)
        self.assertNotIn("UNSAFE_RECOVERY_STATE", self.codes(result))

    def test_worktree_created_before_any_run_record_write(self):
        with self.project() as fixture:
            result = RunService().run(RunRequest(fixture.repo, "Ordering proof", None, fixture.config))
            record = next(Path(result.run_record.feature_worktree, ".agent-workflow", "runs").glob("*/ados-run.json"))
            self.assertEqual("READY_FOR_PUBLICATION", result.status)
            self.assertTrue(record.is_file())
            self.assertTrue(Path(result.run_record.feature_worktree, ".git").exists())

    def test_branch_derived_windows_spaces_and_deterministic_identity(self):
        with tempfile.TemporaryDirectory(prefix="ados run path ") as directory:
            root = Path(directory)
            repo = root / "project repo"
            self.init_repo(repo)
            config = self.write_config(root / "config.json", repo)
            first = RunService().run(RunRequest(repo, "Windows Path Feature", 8, config, dry_run=True))
            second = RunService().run(RunRequest(repo, "Windows Path Feature", 8, config, dry_run=True))

        self.assertEqual("codex/008-windows-path-feature", first.plan.feature_branch)
        self.assertEqual(first.run_record.run_id, second.run_record.run_id)

    def test_run_record_serialization_and_status_sees_active_run(self):
        with self.project() as fixture:
            result = RunService().run(RunRequest(fixture.repo, "Status sees run", None, fixture.config))
            status = StatusService().run(StatusRequest(fixture.repo, fixture.config))

        self.assertEqual("READY_FOR_PUBLICATION", result.run_record.status)
        self.assertEqual(result.run_record.run_id, status.workflow.evidence["run_id"])

    def test_dry_run_has_zero_mutations(self):
        with self.project() as fixture:
            before = self.snapshot(fixture.repo)
            result = RunService().run(RunRequest(fixture.repo, "Plan only", None, fixture.config, dry_run=True))
            after = self.snapshot(fixture.repo)

        self.assertEqual("PLANNED", result.status)
        self.assertEqual(before, after)

    def test_partial_worktree_failure_is_truthful(self):
        with self.project() as fixture:
            planned = fixture.root / "project-failing-create"
            planned.mkdir()
            result = RunService().run(RunRequest(fixture.repo, "Failing create", None, fixture.config))

        self.assertEqual("BLOCKED", result.status)
        self.assertIn("WORKTREE_PATH_EXISTS", self.codes(result))

    def test_no_validation_review_or_publication_side_effects(self):
        with self.project() as fixture:
            result = RunService().run(RunRequest(fixture.repo, "No later stages", None, fixture.config))
            output = self.git(fixture.repo, "log", "--oneline").stdout

        self.assertEqual("READY_FOR_PUBLICATION", result.status)
        self.assertNotIn("validation", output.lower())

    def test_repeated_invocation_detects_existing_run(self):
        with self.project() as fixture:
            first = RunService().run(RunRequest(fixture.repo, "Repeatable", None, fixture.config))
            second = RunService().run(RunRequest(fixture.repo, "Repeatable", None, fixture.config))

        self.assertEqual("READY_FOR_PUBLICATION", first.status)
        self.assertEqual("READY_FOR_PUBLICATION", second.status)
        self.assertTrue(second.resumed)

    def test_ready_for_implementation_resumes_existing_run(self):
        with self.project() as fixture:
            record_path, record = self.create_durable_run(fixture, "Resume me", 8, "READY_FOR_IMPLEMENTATION")
            result = RunService().run(RunRequest(fixture.repo, "Resume me", 8, fixture.config))
            updated = json.loads(record_path.read_text(encoding="utf-8"))

        self.assertTrue(result.resumed)
        self.assertEqual("READY_FOR_PUBLICATION", result.status)
        self.assertEqual(record["runId"], result.run_record.run_id)
        self.assertEqual(record["featureWorktree"], result.run_record.feature_worktree)
        self.assertEqual("READY_FOR_PUBLICATION", updated["status"])

    def test_auto_spec_retry_reuses_existing_resumable_spec(self):
        with self.project(specs=[1]) as fixture:
            record_path, record = self.create_durable_run(fixture, "Auto resume", None, "READY_FOR_IMPLEMENTATION")
            result = RunService().run(RunRequest(fixture.repo, "Auto resume", None, fixture.config))
            updated = json.loads(record_path.read_text(encoding="utf-8"))

        self.assertTrue(result.resumed)
        self.assertEqual("002", result.plan.spec_number)
        self.assertEqual(record["runId"], result.run_record.run_id)
        self.assertEqual("READY_FOR_PUBLICATION", updated["status"])

    def test_failed_and_timed_out_runs_resume(self):
        for status in ("IMPLEMENTATION_FAILED", "IMPLEMENTATION_TIMED_OUT"):
            with self.project() as fixture:
                _record_path, record = self.create_durable_run(fixture, "Retry me", 8, status)
                result = RunService().run(RunRequest(fixture.repo, "Retry me", 8, fixture.config))

            self.assertTrue(result.resumed)
            self.assertEqual("READY_FOR_PUBLICATION", result.status)
            self.assertEqual(record["runId"], result.run_record.run_id)

    def test_resume_identity_mismatch_blocks(self):
        cases = {
            "different_spec": ("specNumber", "009", "Different feature", 8),
            "different_project": ("projectId", "other-project", "Resume me", 8),
            "different_branch": ("featureBranch", "codex/008-other-branch", "Resume me", 8),
            "different_worktree": ("featureWorktree", "C:/elsewhere", "Resume me", 8),
        }
        for _name, (field, value, feature, spec) in cases.items():
            with self.project() as fixture:
                record_path, record = self.create_durable_run(fixture, "Resume me", 8, "READY_FOR_IMPLEMENTATION")
                record[field] = value
                record_path.write_text(json.dumps(record, indent=2, sort_keys=True), encoding="utf-8")
                result = RunService().run(RunRequest(fixture.repo, feature, spec, fixture.config, dry_run=True))

            self.assertEqual("BLOCKED", result.status)
            self.assertFalse(result.resumed)

    def test_duplicate_unrelated_run_blocks(self):
        with self.project() as fixture:
            self.create_durable_run(fixture, "First active", 8, "READY_FOR_IMPLEMENTATION")
            self.create_durable_run(fixture, "Second active", 9, "READY_FOR_IMPLEMENTATION")
            result = RunService().run(RunRequest(fixture.repo, "First active", 8, fixture.config, dry_run=True))

        self.assertEqual("BLOCKED", result.status)
        self.assertIn("UNSAFE_RECOVERY_STATE", self.codes(result))

    def test_resume_invokes_implementer_once_and_completion_does_not_rerun(self):
        with self.project(implementer_mode="count") as fixture:
            counter = fixture.root / "implementer-count.txt"
            self.create_durable_run(fixture, "Counted resume", 8, "READY_FOR_IMPLEMENTATION")
            first = RunService().run(RunRequest(fixture.repo, "Counted resume", 8, fixture.config))
            second = RunService().run(RunRequest(fixture.repo, "Counted resume", 8, fixture.config))
            count = counter.read_text(encoding="utf-8")

        self.assertTrue(first.resumed)
        self.assertEqual("READY_FOR_PUBLICATION", first.status)
        self.assertEqual("READY_FOR_PUBLICATION", second.status)
        self.assertEqual("1", count)

    def test_aiverse_production_ready_run_shape_resumes(self):
        with self.project(project_id="AIverse", allowed_paths=[".claude"]) as fixture:
            record_path, _record = self.create_durable_run(
                fixture,
                "Post-Validation Re-Review Decision & Continuation Foundation",
                84,
                "READY_FOR_IMPLEMENTATION",
            )
            result = RunService().run(
                RunRequest(
                    fixture.repo,
                    "Post-Validation Re-Review Decision & Continuation Foundation",
                    84,
                    fixture.config,
                )
            )
            updated = json.loads(record_path.read_text(encoding="utf-8"))

        self.assertTrue(result.resumed)
        self.assertEqual("READY_FOR_PUBLICATION", result.status)
        self.assertEqual("084", updated["specNumber"])

    def test_project_neutral_and_ados_self_hosting_shape(self):
        with self.project(project_id="other") as fixture:
            result = RunService().run(RunRequest(fixture.repo, "Self hosting shape", None, fixture.config, dry_run=True))

        self.assertEqual("other", result.run_record.project_id)
        self.assertEqual("PLANNED", result.status)

    def test_json_output(self):
        with self.project() as fixture:
            code, stdout = self.run_cli("run", "--project", str(fixture.repo), "--config", str(fixture.config), "--feature", "Json run", "--dry-run", "--json")

        self.assertEqual(0, code)
        self.assertEqual("PLANNED", json.loads(stdout)["status"])

    def test_cli_implementer_failure_exit_one(self):
        with self.project(implementer_mode="failure") as fixture:
            code, stdout = self.run_cli("run", "--project", str(fixture.repo), "--config", str(fixture.config), "--feature", "Failing implementation", "--json")

        self.assertEqual(1, code)
        self.assertEqual("IMPLEMENTATION_FAILED", json.loads(stdout)["status"])

    def test_cli_implementer_timeout_exit_one(self):
        with self.project(implementer_mode="timeout") as fixture:
            code, stdout = self.run_cli(
                "run",
                "--project",
                str(fixture.repo),
                "--config",
                str(fixture.config),
                "--feature",
                "Timed implementation",
                "--implementer-timeout-ms",
                "100",
                "--json",
            )

        self.assertEqual(1, code)
        self.assertEqual("IMPLEMENTATION_TIMED_OUT", json.loads(stdout)["status"])

    def test_end_to_end_pipeline_publishes_merges_and_cleans_up_with_provider(self):
        with self.project() as fixture:
            publisher = FakePublisher(fixture.repo)
            result = RunService(pipeline=RunPipeline(publisher=publisher)).run(RunRequest(fixture.repo, "Ship pipeline", None, fixture.config))
            status = StatusService().run(StatusRequest(fixture.repo, fixture.config))

        self.assertEqual("COMPLETE", result.status)
        self.assertEqual("MERGED", result.pipeline_result.merge.status)
        self.assertFalse(Path(result.plan.feature_worktree).exists())
        self.assertEqual("IDLE", status.status)
        self.assertEqual("001", status.spec.evidence["latest_merged_spec"])
        self.assertEqual(["push", "create_pr", "ready", "refresh_pr", "merge", "delete_remote"], publisher.calls)

    def test_no_change_run_terminates_before_review_and_publication(self):
        with self.project(implementer_mode="no_change") as fixture:
            publisher = FakePublisher(fixture.repo)
            before_head = self.head(fixture.repo)
            result = RunService(pipeline=RunPipeline(publisher=publisher)).run(RunRequest(fixture.repo, "Already satisfied", None, fixture.config))
            status = StatusService().run(StatusRequest(fixture.repo, fixture.config))
            after_head = self.head(fixture.repo)

        self.assertEqual("NO_CHANGES", result.status)
        self.assertEqual(before_head, after_head)
        self.assertIsNone(result.pipeline_result.validation)
        self.assertIsNone(result.pipeline_result.review)
        self.assertEqual([], publisher.calls)
        self.assertFalse(Path(result.plan.feature_worktree).exists())
        self.assertEqual("IDLE", status.status)
        self.assertEqual("None", status.spec.evidence["active_spec"])

    def test_no_change_run_does_not_increment_latest_merged_spec(self):
        with self.project(specs=[91], implementer_mode="no_change") as fixture:
            self.write_archive(fixture.repo, spec="091-previous", merge_commit=self.head(fixture.repo))
            result = RunService(pipeline=RunPipeline(publisher=FakePublisher(fixture.repo))).run(RunRequest(fixture.repo, "No merge evidence", 92, fixture.config))
            status = StatusService().run(StatusRequest(fixture.repo, fixture.config))

        self.assertEqual("NO_CHANGES", result.status)
        self.assertEqual("091", status.spec.evidence["latest_merged_spec"])
        self.assertEqual("None", status.spec.evidence["active_spec"])

    def test_existing_review_blocked_no_change_run_finalizes_without_review_or_pr(self):
        with self.project(implementer_mode="no_change") as fixture:
            publisher = FakePublisher(fixture.repo)
            record_path, record = self.create_durable_run(fixture, "Legacy no delta", 92, "REVIEW_BLOCKED")
            base = record["authoritativeBaseSha"]
            record["nextStage"] = "recovery"
            record["reviewBlock"] = {"reasonCode": "REVIEW_DECISION_UNAVAILABLE", "transient": False}
            record_path.write_text(json.dumps(record, indent=2, sort_keys=True), encoding="utf-8")
            record_path.with_name("candidate.json").write_text(json.dumps({"status": "COMMITTED", "candidate_sha": base, "changed_files": []}, indent=2, sort_keys=True), encoding="utf-8")
            record_path.with_name("validation-runtime.json").write_text(json.dumps({"status": "PASS", "head_before": base, "head_after": base, "commands": [], "violations": []}, indent=2, sort_keys=True), encoding="utf-8")
            record_path.with_name("review-runtime.json").write_text(json.dumps({"status": "PASS", "decision": "", "reviewed_sha": base, "exit_code": 0, "stdout": "", "stderr": "", "violations": []}, indent=2, sort_keys=True), encoding="utf-8")
            status_before = StatusService().run(StatusRequest(fixture.repo, fixture.config))
            result = RunService(pipeline=RunPipeline(publisher=publisher)).run(RunRequest(fixture.repo, "Legacy no delta", 92, fixture.config))
            status = StatusService().run(StatusRequest(fixture.repo, fixture.config))

        self.assertEqual("no_changes", status_before.workflow.evidence["resume_stage"])
        self.assertTrue(result.resumed)
        self.assertEqual("NO_CHANGES", result.status)
        self.assertEqual([], publisher.calls)
        self.assertFalse(Path(record["featureWorktree"]).exists())
        self.assertEqual("IDLE", status.status)

    def test_review_decision_unavailable_with_real_delta_remains_blocked(self):
        with self.project() as fixture:
            publisher = FakePublisher(fixture.repo)
            record_path, record = self.create_durable_run(fixture, "Real delta unavailable", 92, "REVIEW_BLOCKED")
            worktree = Path(record["featureWorktree"])
            (worktree / "feature.txt").write_text("delta\n", encoding="utf-8")
            self.git(worktree, "add", "feature.txt")
            self.git(worktree, "commit", "-m", "real delta")
            candidate = self.head(worktree)
            record["nextStage"] = "recovery"
            record["reviewBlock"] = {"reasonCode": "REVIEW_DECISION_UNAVAILABLE", "transient": False}
            record_path.write_text(json.dumps(record, indent=2, sort_keys=True), encoding="utf-8")
            record_path.with_name("candidate.json").write_text(json.dumps({"status": "COMMITTED", "candidate_sha": candidate, "changed_files": ["feature.txt"]}, indent=2, sort_keys=True), encoding="utf-8")
            record_path.with_name("validation-runtime.json").write_text(json.dumps({"status": "PASS", "head_before": candidate, "head_after": candidate, "commands": [], "violations": []}, indent=2, sort_keys=True), encoding="utf-8")
            record_path.with_name("review-runtime.json").write_text(json.dumps({"status": "PASS", "decision": "", "reviewed_sha": candidate, "exit_code": 0, "stdout": "", "stderr": "", "violations": []}, indent=2, sort_keys=True), encoding="utf-8")
            result = RunService(pipeline=RunPipeline(publisher=publisher)).run(RunRequest(fixture.repo, "Real delta unavailable", 92, fixture.config, dry_run=True))

        self.assertEqual("BLOCKED", result.status)
        self.assertIn("ACTIVE_WORKTREE_PRESENT", self.codes(result))
        self.assertEqual([], publisher.calls)

    def test_dirty_legacy_no_change_evidence_blocks(self):
        with self.project(implementer_mode="no_change") as fixture:
            record_path, record = self.create_durable_run(fixture, "Dirty no delta", 92, "REVIEW_BLOCKED")
            base = record["authoritativeBaseSha"]
            record_path.with_name("candidate.json").write_text(json.dumps({"status": "COMMITTED", "candidate_sha": base, "changed_files": []}, indent=2, sort_keys=True), encoding="utf-8")
            record_path.with_name("validation-runtime.json").write_text(json.dumps({"status": "PASS", "head_before": base, "head_after": base, "commands": [], "violations": []}, indent=2, sort_keys=True), encoding="utf-8")
            (Path(record["featureWorktree"]) / "scratch.txt").write_text("ambiguous\n", encoding="utf-8")
            result = RunService(pipeline=RunPipeline(publisher=FakePublisher(fixture.repo))).run(RunRequest(fixture.repo, "Dirty no delta", 92, fixture.config))

        self.assertEqual("REVIEW_BLOCKED", result.status)
        self.assertIn("NO_CHANGE_WORKTREE_DIRTY", {violation.code for violation in result.pipeline_result.violations})

    def test_no_change_cleanup_resume_is_idempotent(self):
        with self.project(implementer_mode="no_change") as fixture:
            lifecycle = FailingOnceLifecycle()
            service = RunService(pipeline=RunPipeline(publisher=FakePublisher(fixture.repo), lifecycle=lifecycle), lifecycle=lifecycle)
            first = service.run(RunRequest(fixture.repo, "Cleanup retry no delta", 92, fixture.config))
            second = service.run(RunRequest(fixture.repo, "Cleanup retry no delta", 92, fixture.config))
            status = StatusService().run(StatusRequest(fixture.repo, fixture.config))

        self.assertEqual("NO_CHANGES_CLEANUP_INCOMPLETE", first.status)
        self.assertEqual("NO_CHANGES", second.status)
        self.assertEqual("IDLE", status.status)

    def test_bootstrap_runs_before_validation(self):
        with self.project() as fixture:
            marker = fixture.root / "bootstrap-marker.txt"
            bootstrap = fixture.root / "bootstrap.py"
            bootstrap.write_text(f"from pathlib import Path\nPath(r'{marker}').write_text('bootstrapped', encoding='utf-8')\n", encoding="utf-8")
            fixture.config = self.write_config(fixture.root / "project-config.json", fixture.repo, bootstrap_commands=[f'"{sys.executable}" "{bootstrap}"'])
            result = RunService(pipeline=RunPipeline(publisher=FakePublisher(fixture.repo))).run(RunRequest(fixture.repo, "Bootstrap order", None, fixture.config))
            marker_exists = marker.exists()

        self.assertEqual("COMPLETE", result.status)
        self.assertTrue(marker_exists)
        self.assertEqual("PASS", result.pipeline_result.stages[0].status)

    def test_bootstrap_failure_blocks_before_validation(self):
        with self.project() as fixture:
            failing = fixture.root / "bootstrap-fail.py"
            failing.write_text("import sys\nsys.exit(7)\n", encoding="utf-8")
            fixture.config = self.write_config(fixture.root / "project-config.json", fixture.repo, bootstrap_commands=[f'"{sys.executable}" "{failing}"'])
            result = RunService().run(RunRequest(fixture.repo, "Bootstrap fails", None, fixture.config))

        self.assertEqual("BOOTSTRAP_FAILED", result.status)
        self.assertIsNone(result.pipeline_result.validation)

    def test_bootstrap_resolves_executable_without_shell(self):
        with self.project() as fixture:
            fixture.config = self.write_config(fixture.root / "project-config.json", fixture.repo, bootstrap_commands=["tool-shim --version"])
            calls = []

            def fake_run(args, **kwargs):
                calls.append((args, kwargs))
                return subprocess.CompletedProcess(args, 0, "ok", "")

            with mock.patch("ados.run_pipeline.shutil.which", return_value=str(fixture.root / "tool-shim.cmd")):
                with mock.patch("ados.run_pipeline.subprocess.run", side_effect=fake_run):
                    result = RunService().pipeline._bootstrap(load_project_config(fixture.config), {"featureWorktree": str(fixture.repo)}, fixture.repo / ".agent-workflow" / "runs" / "001-test" / "ados-run.json")

        self.assertEqual(0, result[0].exit_code)
        self.assertEqual(str(fixture.root / "tool-shim.cmd"), calls[0][0][0])
        self.assertIs(calls[0][1]["shell"], False)
        self.assertEqual("utf-8", calls[0][1]["encoding"])
        self.assertEqual("replace", calls[0][1]["errors"])

    def test_validation_failure_does_not_review(self):
        with self.project() as fixture:
            config = json.loads(fixture.config.read_text(encoding="utf-8"))
            config["execution_policy"]["validation"]["commands"] = ["python -c \"import sys; sys.exit(4)\""]
            fixture.config.write_text(json.dumps(config), encoding="utf-8")
            result = RunService().run(RunRequest(fixture.repo, "Validation fails", None, fixture.config))

        self.assertEqual("VALIDATION_FAILED", result.status)
        self.assertIsNone(result.pipeline_result.review)

    def test_validation_failure_persists_evidence_and_resumes_implementation_recovery(self):
        with self.project() as fixture:
            implementer_counter = fixture.root / "implementer-count.txt"
            bootstrap_counter = fixture.root / "bootstrap-count.txt"
            reviewer_counter = fixture.root / "review-count.txt"
            implementer = fixture.root / "implementer-recovery.py"
            validator = fixture.root / "validator.py"
            bootstrap = fixture.root / "bootstrap.py"
            reviewer = fixture.root / "reviewer.py"
            implementer.write_text(
                "from pathlib import Path\n"
                "import sys\n"
                f"counter = Path(r'{implementer_counter}')\n"
                "value = int(counter.read_text(encoding='utf-8')) if counter.exists() else 0\n"
                "counter.write_text(str(value + 1), encoding='utf-8')\n"
                "handoff = sys.stdin.read()\n"
                "if 'Implementation recovery context:' in handoff:\n"
                "    Path('fix.txt').write_text('fixed', encoding='utf-8')\n"
                "else:\n"
                "    Path('implementation.txt').write_text('implemented', encoding='utf-8')\n",
                encoding="utf-8",
            )
            validator.write_text(
                "from pathlib import Path\n"
                "import sys\n"
                "if not Path('fix.txt').exists():\n"
                "    sys.stdout.buffer.write('validator stdout ☃\\n'.encode('utf-8'))\n"
                "    sys.stderr.buffer.write('validator stderr 🚀\\n'.encode('utf-8'))\n"
                "    sys.exit(6)\n",
                encoding="utf-8",
            )
            bootstrap.write_text(
                "from pathlib import Path\n"
                f"counter = Path(r'{bootstrap_counter}')\n"
                "value = int(counter.read_text(encoding='utf-8')) if counter.exists() else 0\n"
                "counter.write_text(str(value + 1), encoding='utf-8')\n",
                encoding="utf-8",
            )
            reviewer.write_text(
                "from pathlib import Path\n"
                f"counter = Path(r'{reviewer_counter}')\n"
                "value = int(counter.read_text(encoding='utf-8')) if counter.exists() else 0\n"
                "counter.write_text(str(value + 1), encoding='utf-8')\n"
                "print('Approved')\n",
                encoding="utf-8",
            )
            fixture.config = self.write_config(
                fixture.root / "project-config.json",
                fixture.repo,
                implementer=f'"{sys.executable}" "{implementer}"',
                reviewer=f'"{sys.executable}" "{reviewer}"',
                bootstrap_commands=[f'"{sys.executable}" "{bootstrap}"'],
                validation_commands=[f'"{sys.executable}" "{validator}"'],
            )
            first = RunService(pipeline=RunPipeline(publisher=FakePublisher(fixture.repo))).run(RunRequest(fixture.repo, "Validation recovery", None, fixture.config))
            worktree = Path(first.run_record.feature_worktree)
            record_path = worktree / ".agent-workflow" / "runs" / "001-validation-recovery" / "ados-run.json"
            failed_record = json.loads(record_path.read_text(encoding="utf-8"))
            failed_validation = json.loads(record_path.with_name("validation-runtime.json").read_text(encoding="utf-8"))
            candidate_before = json.loads(record_path.with_name("candidate.json").read_text(encoding="utf-8"))["candidate_sha"]
            status_after_failure = StatusService().run(StatusRequest(fixture.repo, fixture.config))
            second = RunService(pipeline=RunPipeline(publisher=FakePublisher(fixture.repo))).run(RunRequest(fixture.repo, "Validation recovery", None, fixture.config))
            completed_record = json.loads((fixture.repo / ".agent-workflow" / "runs" / "001-validation-recovery" / "ados-run.json").read_text(encoding="utf-8"))
            candidate_after = second.pipeline_result.candidate.candidate_sha
            implementer_count = implementer_counter.read_text(encoding="utf-8")
            bootstrap_count = bootstrap_counter.read_text(encoding="utf-8")
            reviewer_count = reviewer_counter.read_text(encoding="utf-8")

        self.assertEqual("VALIDATION_FAILED", first.status)
        self.assertEqual("implementation_recovery", failed_record["nextStage"])
        self.assertEqual(candidate_before, failed_record["validationFailure"]["candidateSha"])
        self.assertEqual(candidate_before, failed_validation["head_before"])
        self.assertEqual(candidate_before, failed_validation["head_after"])
        self.assertEqual("VALIDATION_COMMAND_FAILED", failed_record["validationFailure"]["failedCommands"][0]["reasonCode"])
        self.assertEqual("6", failed_record["validationFailure"]["failedCommands"][0]["exitCode"])
        self.assertIn("validator stdout", failed_record["validationFailure"]["failedCommands"][0]["stdout"])
        self.assertIn("validator stderr", failed_record["validationFailure"]["failedCommands"][0]["stderr"])
        self.assertEqual("implementation_recovery", status_after_failure.workflow.evidence["resume_stage"])
        self.assertEqual(candidate_before, status_after_failure.workflow.evidence["candidate_sha"])
        self.assertIn("validator.py", status_after_failure.workflow.evidence["failed_validation_commands"])
        self.assertTrue(second.resumed)
        self.assertEqual("COMPLETE", second.status)
        self.assertNotIn("validationFailure", completed_record)
        self.assertNotEqual(candidate_before, candidate_after)
        self.assertEqual("2", implementer_count)
        self.assertEqual("1", bootstrap_count)
        self.assertEqual("1", reviewer_count)

    def test_validation_failure_adopts_clean_new_worktree_head_without_implementer(self):
        with self.project(implementer_mode="count") as fixture:
            implementer_counter = fixture.root / "implementer-count.txt"
            reviewer_counter = fixture.root / "review-count.txt"
            validator = fixture.root / "validator.py"
            reviewer = fixture.root / "reviewer.py"
            validator.write_text(
                "from pathlib import Path\n"
                "import sys\n"
                "if not Path('manual-fix.txt').exists():\n"
                "    sys.exit(6)\n",
                encoding="utf-8",
            )
            reviewer.write_text(
                "from pathlib import Path\n"
                f"counter = Path(r'{reviewer_counter}')\n"
                "value = int(counter.read_text(encoding='utf-8')) if counter.exists() else 0\n"
                "counter.write_text(str(value + 1), encoding='utf-8')\n"
                "print('Approved')\n",
                encoding="utf-8",
            )
            fixture.config = self.write_config(
                fixture.root / "project-config.json",
                fixture.repo,
                implementer_mode="count",
                reviewer=f'"{sys.executable}" "{reviewer}"',
                validation_commands=[f'"{sys.executable}" "{validator}"'],
            )
            first = RunService(pipeline=RunPipeline(publisher=FakePublisher(fixture.repo))).run(RunRequest(fixture.repo, "Manual validation recovery", None, fixture.config))
            worktree = Path(first.run_record.feature_worktree)
            record_path = worktree / ".agent-workflow" / "runs" / "001-manual-validation-recovery" / "ados-run.json"
            failed_candidate = json.loads(record_path.with_name("candidate.json").read_text(encoding="utf-8"))["candidate_sha"]
            (worktree / "manual-fix.txt").write_text("fixed\n", encoding="utf-8")
            self.git(worktree, "add", "manual-fix.txt")
            self.git(worktree, "commit", "-m", "manual validation recovery")
            adopted_candidate = self.head(worktree)

            second = RunService(pipeline=RunPipeline(publisher=FakePublisher(fixture.repo))).run(RunRequest(fixture.repo, "Manual validation recovery", None, fixture.config))

            stages = [stage.id for stage in second.pipeline_result.stages]
            adoption = second.pipeline_result.run_record.get("recoveryCandidateAdoption", {})
            implementer_count = implementer_counter.read_text(encoding="utf-8")
            reviewer_count = reviewer_counter.read_text(encoding="utf-8")

        self.assertEqual("VALIDATION_FAILED", first.status)
        self.assertEqual("COMPLETE", second.status)
        self.assertIn("recovery_candidate_adoption", stages)
        self.assertNotIn("implementer_fix", stages)
        self.assertEqual(adopted_candidate, second.pipeline_result.candidate.candidate_sha)
        self.assertEqual(adopted_candidate, second.pipeline_result.validation.head_after)
        self.assertEqual(adopted_candidate, second.pipeline_result.review.reviewed_sha)
        self.assertEqual(failed_candidate, adoption["previousFailedCandidateSha"])
        self.assertEqual(adopted_candidate, adoption["adoptedCandidateSha"])
        self.assertEqual(["manual-fix.txt"], adoption["adoptedChangedFiles"])
        self.assertEqual(("manual-fix.txt",), second.pipeline_result.candidate.changed_files)
        self.assertEqual("1", implementer_count)
        self.assertEqual("1", reviewer_count)

    def test_adopted_validation_recovery_candidate_can_fail_validation(self):
        with self.project(implementer_mode="count") as fixture:
            implementer_counter = fixture.root / "implementer-count.txt"
            validator = fixture.root / "validator.py"
            validator.write_text(
                "from pathlib import Path\n"
                "import sys\n"
                "if not Path('never-present.txt').exists():\n"
                "    sys.exit(6)\n",
                encoding="utf-8",
            )
            fixture.config = self.write_config(
                fixture.root / "project-config.json",
                fixture.repo,
                implementer_mode="count",
                validation_commands=[f'"{sys.executable}" "{validator}"'],
            )
            first = RunService(pipeline=RunPipeline(publisher=FakePublisher(fixture.repo))).run(RunRequest(fixture.repo, "Manual bad recovery", None, fixture.config))
            worktree = Path(first.run_record.feature_worktree)
            (worktree / "manual-still-bad.txt").write_text("still bad\n", encoding="utf-8")
            self.git(worktree, "add", "manual-still-bad.txt")
            self.git(worktree, "commit", "-m", "manual bad recovery")
            adopted_candidate = self.head(worktree)

            second = RunService(pipeline=RunPipeline(publisher=FakePublisher(fixture.repo))).run(RunRequest(fixture.repo, "Manual bad recovery", None, fixture.config))

            stages = [stage.id for stage in second.pipeline_result.stages]
            adoption = second.pipeline_result.run_record.get("recoveryCandidateAdoption", {})
            adopted_candidate_artifact = json.loads((worktree / ".agent-workflow" / "runs" / "001-manual-bad-recovery" / "candidate.json").read_text(encoding="utf-8"))
            previous_candidate_artifact_exists = Path(adoption["previousCandidateArtifact"]).exists()
            previous_validation_artifact_exists = Path(adoption["previousValidationArtifact"]).exists()
            implementer_count = implementer_counter.read_text(encoding="utf-8")

        self.assertEqual("VALIDATION_FAILED", second.status)
        self.assertIn("recovery_candidate_adoption", stages)
        self.assertEqual(adopted_candidate, second.pipeline_result.candidate.candidate_sha)
        self.assertEqual(adopted_candidate, second.pipeline_result.validation.head_after)
        self.assertEqual(["manual-still-bad.txt"], adopted_candidate_artifact["changed_files"])
        self.assertTrue(previous_candidate_artifact_exists)
        self.assertTrue(previous_validation_artifact_exists)
        self.assertIsNone(second.pipeline_result.review)
        self.assertEqual("1", implementer_count)

    def test_validation_recovery_adoption_blocks_dirty_new_head(self):
        with self.project(implementer_mode="count") as fixture:
            validator = fixture.root / "validator.py"
            validator.write_text("import sys\nsys.exit(6)\n", encoding="utf-8")
            fixture.config = self.write_config(
                fixture.root / "project-config.json",
                fixture.repo,
                implementer_mode="count",
                validation_commands=[f'"{sys.executable}" "{validator}"'],
            )
            first = RunService(pipeline=RunPipeline(publisher=FakePublisher(fixture.repo))).run(RunRequest(fixture.repo, "Dirty manual recovery", None, fixture.config))
            worktree = Path(first.run_record.feature_worktree)
            (worktree / "manual.txt").write_text("manual\n", encoding="utf-8")
            self.git(worktree, "add", "manual.txt")
            self.git(worktree, "commit", "-m", "manual recovery")
            (worktree / "dirty.txt").write_text("dirty\n", encoding="utf-8")

            second = RunService(pipeline=RunPipeline(publisher=FakePublisher(fixture.repo))).run(RunRequest(fixture.repo, "Dirty manual recovery", None, fixture.config))

        self.assertEqual("VALIDATION_FAILED", second.status)
        self.assertIn("RECOVERY_ADOPTION_WORKTREE_DIRTY", {violation.code for violation in second.pipeline_result.violations})

    def test_validation_recovery_adoption_blocks_wrong_branch(self):
        with self.project(implementer_mode="count") as fixture:
            validator = fixture.root / "validator.py"
            validator.write_text("import sys\nsys.exit(6)\n", encoding="utf-8")
            fixture.config = self.write_config(
                fixture.root / "project-config.json",
                fixture.repo,
                implementer_mode="count",
                validation_commands=[f'"{sys.executable}" "{validator}"'],
            )
            first = RunService(pipeline=RunPipeline(publisher=FakePublisher(fixture.repo))).run(RunRequest(fixture.repo, "Wrong branch manual recovery", None, fixture.config))
            worktree = Path(first.run_record.feature_worktree)
            record_path = worktree / ".agent-workflow" / "runs" / "001-wrong-branch-manual-recovery" / "ados-run.json"
            record = json.loads(record_path.read_text(encoding="utf-8"))
            candidate_artifact = json.loads(record_path.with_name("candidate.json").read_text(encoding="utf-8"))
            validation_artifact = json.loads(record_path.with_name("validation-runtime.json").read_text(encoding="utf-8"))
            (worktree / "manual.txt").write_text("manual\n", encoding="utf-8")
            self.git(worktree, "add", "manual.txt")
            self.git(worktree, "commit", "-m", "manual recovery")
            self.git(worktree, "checkout", "-b", "unexpected-recovery-branch")

            config = load_project_config(fixture.config)
            adoption = RunPipeline(publisher=FakePublisher(fixture.repo))._adopt_validation_recovery_candidate(
                config,
                record_path,
                record,
                candidate_artifact,
                validation_artifact,
                [],
            )

        self.assertEqual("VALIDATION_FAILED", adoption.status)
        self.assertIn("RECOVERY_ADOPTION_BRANCH_MISMATCH", {violation.code for violation in adoption.violations})

    def test_legacy_validation_failure_status_reports_artifact_commands(self):
        with self.project() as fixture:
            record_path, record = self.create_durable_run(fixture, "Legacy validation failure", 1, "VALIDATION_FAILED")
            worktree = Path(record["featureWorktree"])
            (worktree / "implementation.txt").write_text("implemented\n", encoding="utf-8")
            self.git(worktree, "add", "implementation.txt")
            self.git(worktree, "commit", "-m", "implementation")
            candidate_sha = self.head(worktree)
            record_path.with_name("candidate.json").write_text(
                json.dumps({"status": "COMMITTED", "candidate_sha": candidate_sha, "changed_files": ["implementation.txt"]}, indent=2, sort_keys=True),
                encoding="utf-8",
            )
            record_path.with_name("validation-runtime.json").write_text(
                json.dumps(
                    {
                        "status": "BLOCK",
                        "head_before": candidate_sha,
                        "head_after": candidate_sha,
                        "commands": [{"command": "npx tsc --noEmit", "exit_code": 2, "stdout": "type error", "stderr": "TS18048"}],
                        "violations": [{"code": "VALIDATION_COMMAND_FAILED", "message": "validation command failed", "evidence": {"command": "npx tsc --noEmit"}}],
                    },
                    indent=2,
                    sort_keys=True,
                ),
                encoding="utf-8",
            )
            status = StatusService().run(StatusRequest(fixture.repo, fixture.config))

        self.assertEqual("VALIDATION_FAILED", status.workflow.evidence["run_status"])
        self.assertEqual("implementation_recovery", status.workflow.evidence["resume_stage"])
        self.assertEqual(candidate_sha, status.workflow.evidence["candidate_sha"])
        self.assertIn("npx tsc --noEmit", status.workflow.evidence["failed_validation_commands"])

    def test_validation_failed_malformed_evidence_does_not_resume(self):
        with self.project() as fixture:
            record_path, record = self.create_durable_run(fixture, "Broken validation evidence", 1, "VALIDATION_FAILED")
            record_path.with_name("candidate.json").write_text(json.dumps({"status": "COMMITTED", "candidate_sha": self.head(Path(record["featureWorktree"])), "changed_files": []}), encoding="utf-8")
            result = RunService().run(RunRequest(fixture.repo, "Broken validation evidence", 1, fixture.config, dry_run=True))

        self.assertEqual("BLOCKED", result.status)
        self.assertFalse(result.resumed)
        self.assertIn("ACTIVE_WORKTREE_PRESENT", self.codes(result))

    def test_review_changes_requested_enters_bounded_fix_loop(self):
        with self.project(implementer_mode="count") as fixture:
            reviewer = fixture.root / "reviewer.py"
            counter = fixture.root / "review-count.txt"
            reviewer.write_text(
                "from pathlib import Path\n"
                f"counter = Path(r'{counter}')\n"
                "value = int(counter.read_text(encoding='utf-8')) if counter.exists() else 0\n"
                "counter.write_text(str(value + 1), encoding='utf-8')\n"
                "print('Changes Requested' if value == 0 else 'Approved')\n",
                encoding="utf-8",
            )
            fixture.config = self.write_config(fixture.root / "project-config.json", fixture.repo, implementer_mode="count", reviewer=f'"{sys.executable}" "{reviewer}"')
            result = RunService(pipeline=RunPipeline(publisher=FakePublisher(fixture.repo))).run(RunRequest(fixture.repo, "Fix loop", None, fixture.config))
            review_count = counter.read_text(encoding="utf-8")

        self.assertEqual("COMPLETE", result.status)
        self.assertEqual("2", review_count)
        self.assertIn("implementer_fix", [stage.id for stage in result.pipeline_result.stages])

    def test_review_blocked_transient_failure_resumes_at_review_and_completes(self):
        with self.project(implementer_mode="count") as fixture:
            implementer_counter = fixture.root / "implementer-count.txt"
            reviewer = fixture.root / "reviewer.py"
            reviewer_counter = fixture.root / "review-count.txt"
            reviewer.write_text(
                "from pathlib import Path\n"
                "import sys\n"
                f"counter = Path(r'{reviewer_counter}')\n"
                "value = int(counter.read_text(encoding='utf-8')) if counter.exists() else 0\n"
                "counter.write_text(str(value + 1), encoding='utf-8')\n"
                "if value == 0:\n"
                "    print('reviewer temporarily unavailable', file=sys.stderr)\n"
                "    sys.exit(7)\n"
                "print('Approved')\n",
                encoding="utf-8",
            )
            fixture.config = self.write_config(fixture.root / "project-config.json", fixture.repo, implementer_mode="count", reviewer=f'"{sys.executable}" "{reviewer}"')
            publisher = FakePublisher(fixture.repo)
            service = RunService(pipeline=RunPipeline(publisher=publisher))
            first = service.run(RunRequest(fixture.repo, "Transient review outage", None, fixture.config))
            worktree = Path(first.run_record.feature_worktree)
            record_path = worktree / ".agent-workflow" / "runs" / "001-transient-review-outage" / "ados-run.json"
            candidate_path = worktree / ".agent-workflow" / "runs" / "001-transient-review-outage" / "candidate.json"
            blocked_record = json.loads(record_path.read_text(encoding="utf-8"))
            candidate_before = json.loads(candidate_path.read_text(encoding="utf-8"))["candidate_sha"]
            status = StatusService().run(StatusRequest(fixture.repo, fixture.config))

            second = service.run(RunRequest(fixture.repo, "Transient review outage", None, fixture.config))
            implementer_count = implementer_counter.read_text(encoding="utf-8")
            reviewer_count = reviewer_counter.read_text(encoding="utf-8")

        self.assertEqual("REVIEW_BLOCKED", first.status)
        self.assertTrue(blocked_record["reviewBlock"]["transient"])
        self.assertEqual("REVIEWER_COMMAND_FAILED", blocked_record["reviewBlock"]["reasonCode"])
        self.assertEqual("review", blocked_record["reviewBlock"]["resumeStage"])
        self.assertEqual("REVIEW_BLOCKED", status.workflow.evidence["run_status"])
        self.assertEqual("True", status.workflow.evidence["resumable"])
        self.assertEqual("REVIEWER_COMMAND_FAILED", status.workflow.evidence["review_block_reason"])
        self.assertEqual("review", status.workflow.evidence["resume_stage"])
        self.assertTrue(second.resumed)
        self.assertEqual("COMPLETE", second.status)
        self.assertEqual("1", implementer_count)
        self.assertEqual("2", reviewer_count)
        self.assertEqual(candidate_before, second.pipeline_result.review.reviewed_sha)

    def test_review_changes_requested_block_adopts_clean_new_worktree_head_without_implementer(self):
        with self.project(implementer_mode="count") as fixture:
            implementer_counter = fixture.root / "implementer-count.txt"
            validation_counter = fixture.root / "validation-count.txt"
            reviewer_counter = fixture.root / "review-count.txt"
            validator = fixture.root / "validator.py"
            reviewer = fixture.root / "reviewer.py"
            validator.write_text(
                "from pathlib import Path\n"
                f"counter = Path(r'{validation_counter}')\n"
                "value = int(counter.read_text(encoding='utf-8')) if counter.exists() else 0\n"
                "counter.write_text(str(value + 1), encoding='utf-8')\n",
                encoding="utf-8",
            )
            reviewer.write_text(
                "from pathlib import Path\n"
                f"counter = Path(r'{reviewer_counter}')\n"
                "value = int(counter.read_text(encoding='utf-8')) if counter.exists() else 0\n"
                "counter.write_text(str(value + 1), encoding='utf-8')\n"
                "print('Approved')\n",
                encoding="utf-8",
            )
            fixture.config = self.write_config(
                fixture.root / "project-config.json",
                fixture.repo,
                implementer_mode="count",
                reviewer=f'"{sys.executable}" "{reviewer}"',
                validation_commands=[f'"{sys.executable}" "{validator}"'],
            )
            record_path, record, reviewed_candidate = self.create_review_changes_requested_blocked_run(fixture, "Manual review recovery", 1)
            worktree = Path(record["featureWorktree"])
            record["recoveryCandidateAdoption"] = {
                "status": "ADOPTED",
                "adoptedCandidateSha": reviewed_candidate,
                "adoptedChangedFiles": ["stale-validation-recovery.txt"],
            }
            record_path.write_text(json.dumps(record, indent=2, sort_keys=True), encoding="utf-8")
            (worktree / "manual-review-fix.txt").write_text("fixed\n", encoding="utf-8")
            self.git(worktree, "add", "manual-review-fix.txt")
            self.git(worktree, "commit", "-m", "manual review recovery")
            adopted_candidate = self.head(worktree)
            status = StatusService().run(StatusRequest(fixture.repo, fixture.config))

            result = RunService(pipeline=RunPipeline(publisher=FakePublisher(fixture.repo))).run(RunRequest(fixture.repo, "Manual review recovery", 1, fixture.config))
            stages = [stage.id for stage in result.pipeline_result.stages]
            adoption = result.pipeline_result.run_record.get("reviewChangesRecoveryAdoption", {})

        self.assertEqual("REVIEW_BLOCKED", status.workflow.evidence["run_status"])
        self.assertEqual("True", status.workflow.evidence["resumable"])
        self.assertEqual("REVIEW_CHANGES_REQUESTED", status.workflow.evidence["review_block_reason"])
        self.assertEqual("False", status.workflow.evidence["review_block_transient"])
        self.assertEqual("implementation_recovery", status.workflow.evidence["resume_stage"])
        self.assertTrue(result.resumed)
        self.assertEqual("COMPLETE", result.status)
        self.assertEqual("ADOPTED", adoption["status"])
        self.assertEqual("SKIPPED", next(stage.status for stage in result.pipeline_result.stages if stage.id == "implementer"))
        self.assertEqual(reviewed_candidate, adoption["previousReviewedCandidateSha"])
        self.assertEqual(adopted_candidate, adoption["adoptedCandidateSha"])
        self.assertEqual(["manual-review-fix.txt"], adoption["adoptedChangedFiles"])
        self.assertEqual(adopted_candidate, result.pipeline_result.candidate.candidate_sha)
        self.assertEqual(("manual-review-fix.txt",), result.pipeline_result.candidate.changed_files)
        self.assertEqual(adopted_candidate, result.pipeline_result.validation.head_after)
        self.assertEqual(adopted_candidate, result.pipeline_result.review.reviewed_sha)
        self.assertIn("previousReviewArtifact", adoption)
        self.assertFalse(implementer_counter.exists())
        self.assertEqual(1, len(result.pipeline_result.validation.commands))
        self.assertIn("validator.py", result.pipeline_result.validation.commands[0].command)
        self.assertEqual("Approved", result.pipeline_result.review.decision)
        self.assertEqual("Approved\n", result.pipeline_result.review.stdout)

    def test_review_changes_requested_block_from_max_rounds_is_resumable(self):
        with self.project(implementer_mode="count") as fixture:
            reviewer_counter = fixture.root / "review-count.txt"
            reviewer = fixture.root / "reviewer.py"
            reviewer.write_text(
                "from pathlib import Path\n"
                f"counter = Path(r'{reviewer_counter}')\n"
                "value = int(counter.read_text(encoding='utf-8')) if counter.exists() else 0\n"
                "counter.write_text(str(value + 1), encoding='utf-8')\n"
                "print('Changes Requested' if value == 0 else 'Approved')\n",
                encoding="utf-8",
            )
            fixture.config = self.write_config(
                fixture.root / "project-config.json",
                fixture.repo,
                implementer_mode="count",
                reviewer=f'"{sys.executable}" "{reviewer}"',
            )
            config = json.loads(fixture.config.read_text(encoding="utf-8"))
            config["execution_policy"]["review"]["max_rounds"] = 1
            fixture.config.write_text(json.dumps(config), encoding="utf-8")
            first = RunService(pipeline=RunPipeline(publisher=FakePublisher(fixture.repo))).run(RunRequest(fixture.repo, "Max round review recovery", 1, fixture.config))
            worktree = Path(first.run_record.feature_worktree)
            record_path = worktree / ".agent-workflow" / "runs" / "001-max-round-review-recovery" / "ados-run.json"
            blocked_record = json.loads(record_path.read_text(encoding="utf-8"))
            (worktree / "manual-review-fix.txt").write_text("fixed\n", encoding="utf-8")
            self.git(worktree, "add", "manual-review-fix.txt")
            self.git(worktree, "commit", "-m", "manual review recovery")
            adopted_candidate = self.head(worktree)
            status = StatusService().run(StatusRequest(fixture.repo, fixture.config))
            second = RunService(pipeline=RunPipeline(publisher=FakePublisher(fixture.repo))).run(RunRequest(fixture.repo, "Max round review recovery", 1, fixture.config))
            adoption = second.pipeline_result.run_record.get("reviewChangesRecoveryAdoption", {})

        self.assertEqual("REVIEW_BLOCKED", first.status)
        self.assertEqual("REVIEW_CHANGES_REQUESTED", blocked_record["reviewBlock"]["reasonCode"])
        self.assertEqual("review_decision", blocked_record["reviewBlock"]["blockCause"])
        self.assertEqual("True", status.workflow.evidence["resumable"])
        self.assertTrue(second.resumed)
        self.assertEqual("COMPLETE", second.status)
        self.assertEqual(adopted_candidate, adoption["adoptedCandidateSha"])
        self.assertEqual(adopted_candidate, second.pipeline_result.validation.head_after)
        self.assertEqual(adopted_candidate, second.pipeline_result.review.reviewed_sha)
        self.assertEqual("Approved", second.pipeline_result.review.decision)

    def test_review_changes_requested_resume_admission_survives_primary_base_drift(self):
        with self.project(implementer_mode="count") as fixture:
            record_path, record, _reviewed_candidate = self.create_review_changes_requested_blocked_run(fixture, "Drifted base review recovery", 1)
            worktree = Path(record["featureWorktree"])
            (worktree / "manual-review-fix.txt").write_text("fixed\n", encoding="utf-8")
            self.git(worktree, "add", "manual-review-fix.txt")
            self.git(worktree, "commit", "-m", "manual review recovery")
            adopted_candidate = self.head(worktree)
            (fixture.repo / "main-drift.txt").write_text("main moved\n", encoding="utf-8")
            self.git(fixture.repo, "add", "main-drift.txt")
            self.git(fixture.repo, "commit", "-m", "advance main")
            self.git(fixture.repo, "update-ref", "refs/remotes/origin/main", self.head(fixture.repo))
            status = StatusService().run(StatusRequest(fixture.repo, fixture.config))

            result = RunService(pipeline=RunPipeline(publisher=FakePublisher(fixture.repo))).run(RunRequest(fixture.repo, "Drifted base review recovery", 1, fixture.config))
            codes = self.codes(result)

        self.assertEqual("REVIEW_BLOCKED", status.workflow.evidence["run_status"])
        self.assertEqual("True", status.workflow.evidence["resumable"])
        self.assertTrue(result.resumed)
        self.assertEqual("PUBLICATION_BLOCKED", result.status)
        self.assertNotIn("ACTIVE_WORKTREE_PRESENT", codes)
        self.assertNotIn("FEATURE_BRANCH_EXISTS", codes)
        self.assertNotIn("WORKTREE_PATH_EXISTS", codes)
        self.assertNotIn("CONFLICTING_WORKTREE", codes)
        self.assertEqual(adopted_candidate, result.pipeline_result.validation.head_after)
        self.assertEqual(adopted_candidate, result.pipeline_result.review.reviewed_sha)
        self.assertEqual("Approved", result.pipeline_result.review.decision)
        self.assertIn("PR_BASE_SHA_MISMATCH", {violation.code for violation in result.pipeline_result.violations})

    def test_non_resumable_review_block_with_primary_base_drift_still_conflicts(self):
        with self.project(implementer_mode="count") as fixture:
            record_path, record, _reviewed_candidate = self.create_review_changes_requested_blocked_run(fixture, "Drifted base blocked recovery", 1)
            record["reviewBlock"]["reasonCode"] = "REVIEW_DECISION_UNAVAILABLE"
            record["reviewBlock"]["decision"] = "Unavailable"
            record["reviewBlock"].pop("blockCause", None)
            record_path.write_text(json.dumps(record, indent=2, sort_keys=True), encoding="utf-8")
            (fixture.repo / "main-drift.txt").write_text("main moved\n", encoding="utf-8")
            self.git(fixture.repo, "add", "main-drift.txt")
            self.git(fixture.repo, "commit", "-m", "advance main")
            self.git(fixture.repo, "update-ref", "refs/remotes/origin/main", self.head(fixture.repo))

            result = RunService().run(RunRequest(fixture.repo, "Drifted base blocked recovery", 1, fixture.config, dry_run=True))

        self.assertEqual("BLOCKED", result.status)
        self.assertFalse(result.resumed)
        self.assertIn("ACTIVE_WORKTREE_PRESENT", self.codes(result))
        self.assertIn("FEATURE_BRANCH_EXISTS", self.codes(result))
        self.assertIn("WORKTREE_PATH_EXISTS", self.codes(result))
        self.assertIn("CONFLICTING_WORKTREE", self.codes(result))

    def test_same_base_run_id_mismatch_does_not_resume(self):
        with self.project(implementer_mode="count") as fixture:
            record_path, record, _reviewed_candidate = self.create_review_changes_requested_blocked_run(fixture, "Mismatched run identity", 1)
            record["runId"] = "different-run-id"
            record_path.write_text(json.dumps(record, indent=2, sort_keys=True), encoding="utf-8")

            result = RunService().run(RunRequest(fixture.repo, "Mismatched run identity", 1, fixture.config, dry_run=True))

        self.assertEqual("BLOCKED", result.status)
        self.assertFalse(result.resumed)
        self.assertIn("ACTIVE_WORKTREE_PRESENT", self.codes(result))

    def test_auto_spec_resume_admission_survives_primary_base_drift(self):
        with self.project(specs=[1], implementer_mode="count") as fixture:
            record_path, record, _reviewed_candidate = self.create_review_changes_requested_blocked_run(fixture, "Auto drifted base review recovery", None)
            worktree = Path(record["featureWorktree"])
            (worktree / "manual-review-fix.txt").write_text("fixed\n", encoding="utf-8")
            self.git(worktree, "add", "manual-review-fix.txt")
            self.git(worktree, "commit", "-m", "manual review recovery")
            (fixture.repo / "main-drift.txt").write_text("main moved\n", encoding="utf-8")
            self.git(fixture.repo, "add", "main-drift.txt")
            self.git(fixture.repo, "commit", "-m", "advance main")
            self.git(fixture.repo, "update-ref", "refs/remotes/origin/main", self.head(fixture.repo))

            result = RunService(pipeline=RunPipeline(publisher=FakePublisher(fixture.repo))).run(RunRequest(fixture.repo, "Auto drifted base review recovery", None, fixture.config))

        self.assertTrue(result.resumed)
        self.assertEqual("002", result.plan.spec_number)
        self.assertEqual(record["runId"], result.run_record.run_id)
        self.assertEqual("PUBLICATION_BLOCKED", result.status)
        self.assertEqual("Approved", result.pipeline_result.review.decision)
        self.assertIn("PR_BASE_SHA_MISMATCH", {violation.code for violation in result.pipeline_result.violations})

    def test_review_changes_requested_block_reruns_review_when_head_unchanged(self):
        with self.project(implementer_mode="count") as fixture:
            reviewer_counter = fixture.root / "review-count.txt"
            reviewer = fixture.root / "reviewer.py"
            reviewer.write_text(
                "from pathlib import Path\n"
                f"counter = Path(r'{reviewer_counter}')\n"
                "value = int(counter.read_text(encoding='utf-8')) if counter.exists() else 0\n"
                "counter.write_text(str(value + 1), encoding='utf-8')\n"
                "print('Approved')\n",
                encoding="utf-8",
            )
            fixture.config = self.write_config(
                fixture.root / "project-config.json",
                fixture.repo,
                implementer_mode="count",
                reviewer=f'"{sys.executable}" "{reviewer}"',
            )
            _record_path, _record, reviewed_candidate = self.create_review_changes_requested_blocked_run(fixture, "Unchanged review recovery", 1)

            result = RunService(pipeline=RunPipeline(publisher=FakePublisher(fixture.repo))).run(RunRequest(fixture.repo, "Unchanged review recovery", 1, fixture.config))
            stages = [stage.id for stage in result.pipeline_result.stages]

        self.assertTrue(result.resumed)
        self.assertEqual("COMPLETE", result.status)
        self.assertNotIn("review_changes_recovery_adoption", stages)
        self.assertIn("review", stages)
        self.assertEqual(reviewed_candidate, result.pipeline_result.review.reviewed_sha)
        self.assertEqual("Approved", result.pipeline_result.review.decision)
        self.assertEqual("Approved\n", result.pipeline_result.review.stdout)

    def test_review_changes_requested_recovery_blocks_dirty_new_head(self):
        with self.project(implementer_mode="count") as fixture:
            record_path, record, _reviewed_candidate = self.create_review_changes_requested_blocked_run(fixture, "Dirty review recovery", 1)
            worktree = Path(record["featureWorktree"])
            (worktree / "manual-review-fix.txt").write_text("fixed\n", encoding="utf-8")
            self.git(worktree, "add", "manual-review-fix.txt")
            self.git(worktree, "commit", "-m", "manual review recovery")
            (worktree / "dirty.txt").write_text("dirty\n", encoding="utf-8")

            result = RunService(pipeline=RunPipeline(publisher=FakePublisher(fixture.repo))).run(RunRequest(fixture.repo, "Dirty review recovery", 1, fixture.config))

        self.assertEqual("REVIEW_BLOCKED", result.status)
        self.assertIn("REVIEW_RESUME_WORKTREE_DIRTY", {violation.code for violation in result.pipeline_result.violations})

    def test_review_changes_requested_recovery_blocks_wrong_branch(self):
        with self.project(implementer_mode="count") as fixture:
            record_path, record, _reviewed_candidate = self.create_review_changes_requested_blocked_run(fixture, "Wrong branch review recovery", 1)
            worktree = Path(record["featureWorktree"])
            (worktree / "manual-review-fix.txt").write_text("fixed\n", encoding="utf-8")
            self.git(worktree, "add", "manual-review-fix.txt")
            self.git(worktree, "commit", "-m", "manual review recovery")
            self.git(worktree, "checkout", "-b", "unexpected-review-recovery")

            result = RunService(pipeline=RunPipeline(publisher=FakePublisher(fixture.repo))).run(RunRequest(fixture.repo, "Wrong branch review recovery", 1, fixture.config))

        self.assertEqual("BLOCKED", result.status)
        self.assertIn("ACTIVE_WORKTREE_PRESENT", self.codes(result))

    def test_review_blocked_incomplete_output_without_structural_reason_does_not_resume(self):
        with self.project(implementer_mode="count") as fixture:
            validation_counter = fixture.root / "validation-count.txt"
            validation = fixture.root / "validation.py"
            validation.write_text(
                "from pathlib import Path\n"
                f"counter = Path(r'{validation_counter}')\n"
                "value = int(counter.read_text(encoding='utf-8')) if counter.exists() else 0\n"
                "counter.write_text(str(value + 1), encoding='utf-8')\n",
                encoding="utf-8",
            )
            reviewer = fixture.root / "reviewer.py"
            reviewer_counter = fixture.root / "review-count.txt"
            reviewer.write_text(
                "from pathlib import Path\n"
                f"counter = Path(r'{reviewer_counter}')\n"
                "value = int(counter.read_text(encoding='utf-8')) if counter.exists() else 0\n"
                "counter.write_text(str(value + 1), encoding='utf-8')\n"
                "print('Waiting for the background full test suite to finish before issuing the final verdict.' if value == 0 else 'Approved')\n",
                encoding="utf-8",
            )
            fixture.config = self.write_config(
                fixture.root / "project-config.json",
                fixture.repo,
                implementer_mode="count",
                reviewer=f'"{sys.executable}" "{reviewer}"',
            )
            config = json.loads(fixture.config.read_text(encoding="utf-8"))
            config["execution_policy"]["validation"]["commands"] = [f'"{sys.executable}" "{validation}"', "git diff --check"]
            fixture.config.write_text(json.dumps(config), encoding="utf-8")
            publisher = FakePublisher(fixture.repo)
            first = RunService(pipeline=RunPipeline(publisher=publisher)).run(RunRequest(fixture.repo, "Interrupted review output", None, fixture.config))
            worktree = Path(first.run_record.feature_worktree)
            record_path = worktree / ".agent-workflow" / "runs" / "001-interrupted-review-output" / "ados-run.json"
            candidate_path = worktree / ".agent-workflow" / "runs" / "001-interrupted-review-output" / "candidate.json"
            blocked_record = json.loads(record_path.read_text(encoding="utf-8"))
            candidate_before = json.loads(candidate_path.read_text(encoding="utf-8"))["candidate_sha"]
            status = StatusService().run(StatusRequest(fixture.repo, fixture.config))

            second = RunService(pipeline=RunPipeline(publisher=publisher)).run(RunRequest(fixture.repo, "Interrupted review output", None, fixture.config, dry_run=True))
            validation_count = validation_counter.read_text(encoding="utf-8")
            reviewer_count = reviewer_counter.read_text(encoding="utf-8")

        self.assertEqual("REVIEW_BLOCKED", first.status)
        self.assertEqual("REVIEW_DECISION_UNAVAILABLE", blocked_record["reviewBlock"]["reasonCode"])
        self.assertFalse(blocked_record["reviewBlock"]["transient"])
        self.assertEqual("False", status.workflow.evidence["resumable"])
        self.assertEqual("REVIEW_DECISION_UNAVAILABLE", status.workflow.evidence["review_block_reason"])
        self.assertEqual("", status.workflow.evidence["resume_stage"])
        self.assertFalse(second.resumed)
        self.assertEqual("BLOCKED", second.status)
        self.assertEqual("1", validation_count)
        self.assertEqual("1", reviewer_count)
        self.assertEqual(candidate_before, blocked_record["reviewBlock"]["candidateSha"])

    def test_review_blocked_sha_mismatch_does_not_resume(self):
        with self.project(implementer_mode="count") as fixture:
            reviewer = fixture.root / "reviewer.py"
            reviewer.write_text("import sys\nprint('temporary outage', file=sys.stderr)\nsys.exit(7)\n", encoding="utf-8")
            fixture.config = self.write_config(fixture.root / "project-config.json", fixture.repo, implementer_mode="count", reviewer=f'"{sys.executable}" "{reviewer}"')
            first = RunService(pipeline=RunPipeline(publisher=FakePublisher(fixture.repo))).run(RunRequest(fixture.repo, "Corrupt review resume", None, fixture.config))
            worktree = Path(first.run_record.feature_worktree)
            validation_path = worktree / ".agent-workflow" / "runs" / "001-corrupt-review-resume" / "validation-runtime.json"
            validation = json.loads(validation_path.read_text(encoding="utf-8"))
            validation["head_after"] = "0" * 40
            validation_path.write_text(json.dumps(validation, indent=2, sort_keys=True), encoding="utf-8")
            status = StatusService().run(StatusRequest(fixture.repo, fixture.config))
            second = RunService(pipeline=RunPipeline(publisher=FakePublisher(fixture.repo))).run(RunRequest(fixture.repo, "Corrupt review resume", None, fixture.config, dry_run=True))

        self.assertEqual("REVIEW_BLOCKED", first.status)
        self.assertEqual("False", status.workflow.evidence["resumable"])
        self.assertEqual("False", status.workflow.evidence["review_block_transient"])
        self.assertEqual("", status.workflow.evidence["resume_stage"])
        self.assertEqual("BLOCKED", second.status)
        self.assertFalse(second.resumed)
        self.assertIn("ACTIVE_WORKTREE_PRESENT", self.codes(second))

    def test_review_blocked_without_transient_code_does_not_resume(self):
        with self.project(implementer_mode="count") as fixture:
            reviewer = fixture.root / "reviewer.py"
            reviewer.write_text("import sys\nprint('temporary outage', file=sys.stderr)\nsys.exit(7)\n", encoding="utf-8")
            fixture.config = self.write_config(fixture.root / "project-config.json", fixture.repo, implementer_mode="count", reviewer=f'"{sys.executable}" "{reviewer}"')
            first = RunService(pipeline=RunPipeline(publisher=FakePublisher(fixture.repo))).run(RunRequest(fixture.repo, "Malformed review block", None, fixture.config))
            worktree = Path(first.run_record.feature_worktree)
            review_path = worktree / ".agent-workflow" / "runs" / "001-malformed-review-block" / "review-runtime.json"
            review = json.loads(review_path.read_text(encoding="utf-8"))
            review["violations"] = []
            review_path.write_text(json.dumps(review, indent=2, sort_keys=True), encoding="utf-8")
            second = RunService(pipeline=RunPipeline(publisher=FakePublisher(fixture.repo))).run(RunRequest(fixture.repo, "Malformed review block", None, fixture.config, dry_run=True))

        self.assertEqual("REVIEW_BLOCKED", first.status)
        self.assertEqual("BLOCKED", second.status)
        self.assertFalse(second.resumed)
        self.assertIn("ACTIVE_WORKTREE_PRESENT", self.codes(second))

    def test_publication_gate_remote_sha_drift_blocks(self):
        with self.project() as fixture:
            result = RunService(pipeline=RunPipeline(publisher=FakePublisher(fixture.repo, remote_drift=True))).run(RunRequest(fixture.repo, "Drift blocks", None, fixture.config))

        self.assertEqual("PUBLICATION_BLOCKED", result.status)
        self.assertIn("PR_HEAD_SHA_MISMATCH", {violation.code for violation in result.pipeline_result.violations})

    def test_review_approved_resume_continues_publication_without_rerunning_review(self):
        with self.project() as fixture:
            self.create_review_approved_run(fixture, "Publication resume", 1)
            publisher = FakePublisher(fixture.repo)
            result = RunService(pipeline=RunPipeline(publisher=publisher)).run(RunRequest(fixture.repo, "Publication resume", 1, fixture.config))

        self.assertTrue(result.resumed)
        self.assertEqual("COMPLETE", result.status)
        self.assertEqual(["push", "create_pr", "ready", "refresh_pr", "merge", "delete_remote"], publisher.calls)

    def test_publication_retry_after_ready_failure_resumes_and_prevents_duplicate_pr(self):
        with self.project() as fixture:
            record_path, _ = self.create_review_approved_run(fixture, "Retry publication", 1)
            publisher = FakePublisher(fixture.repo, ready_failure_once=True)
            first = RunService(pipeline=RunPipeline(publisher=publisher)).run(RunRequest(fixture.repo, "Retry publication", 1, fixture.config))
            first_record = json.loads(record_path.read_text(encoding="utf-8"))
            second = RunService(pipeline=RunPipeline(publisher=publisher)).run(RunRequest(fixture.repo, "Retry publication", 1, fixture.config))

        self.assertEqual("PUBLICATION_BLOCKED", first.status)
        self.assertEqual("PR_CREATED", first_record["status"])
        self.assertTrue(second.resumed)
        self.assertEqual("COMPLETE", second.status)
        self.assertEqual(1, publisher.created_pr_count)

    def test_publication_resume_refreshes_stale_pr_mergeability_before_gate(self):
        with self.project() as fixture:
            record_path, _ = self.create_review_approved_run(fixture, "Refresh stale PR", 1)
            publisher = FakePublisher(fixture.repo, initial_mergeable=False, refresh_mergeable_sequence=[False, False, False, True])
            first = RunService(pipeline=RunPipeline(publisher=publisher)).run(RunRequest(fixture.repo, "Refresh stale PR", 1, fixture.config))
            first_record = json.loads(record_path.read_text(encoding="utf-8"))
            second = RunService(pipeline=RunPipeline(publisher=publisher)).run(RunRequest(fixture.repo, "Refresh stale PR", 1, fixture.config))

        self.assertEqual("PUBLICATION_BLOCKED", first.status)
        self.assertEqual("false", first_record["prMergeable"])
        self.assertEqual("PR_READY", first_record["status"])
        self.assertTrue(second.resumed)
        self.assertEqual("COMPLETE", second.status)
        self.assertIn("refresh_pr", publisher.calls)
        self.assertEqual(1, publisher.created_pr_count)

    def test_publication_refresh_blocks_wrong_pr_head(self):
        with self.project() as fixture:
            self.create_review_approved_run(fixture, "Wrong PR head", 1)
            publisher = FakePublisher(fixture.repo, refresh_head_sha="0" * 40)
            result = RunService(pipeline=RunPipeline(publisher=publisher)).run(RunRequest(fixture.repo, "Wrong PR head", 1, fixture.config))

        self.assertEqual("PUBLICATION_BLOCKED", result.status)
        self.assertIn("PR_HEAD_SHA_MISMATCH", {violation.code for violation in result.pipeline_result.violations})

    def test_publication_refresh_blocks_wrong_pr_base(self):
        with self.project() as fixture:
            self.create_review_approved_run(fixture, "Wrong PR base", 1)
            publisher = FakePublisher(fixture.repo, refresh_base="develop")
            result = RunService(pipeline=RunPipeline(publisher=publisher)).run(RunRequest(fixture.repo, "Wrong PR base", 1, fixture.config))

        self.assertEqual("PUBLICATION_BLOCKED", result.status)
        self.assertIn("PR_BASE_MISMATCH", {violation.code for violation in result.pipeline_result.violations})

    def test_publication_refresh_blocks_conflict_and_draft(self):
        with self.project() as conflict_fixture:
            self.create_review_approved_run(conflict_fixture, "Conflicting PR", 1)
            conflict = RunService(pipeline=RunPipeline(publisher=FakePublisher(conflict_fixture.repo, initial_mergeable=False, refresh_mergeable_sequence=[False], refresh_merge_state_sequence=["DIRTY"]))).run(RunRequest(conflict_fixture.repo, "Conflicting PR", 1, conflict_fixture.config))
        with self.project() as draft_fixture:
            self.create_review_approved_run(draft_fixture, "Draft PR", 1)
            draft = RunService(pipeline=RunPipeline(publisher=FakePublisher(draft_fixture.repo, refresh_draft=True))).run(RunRequest(draft_fixture.repo, "Draft PR", 1, draft_fixture.config))

        self.assertEqual("PUBLICATION_BLOCKED", conflict.status)
        self.assertIn("PR_NOT_MERGEABLE", {violation.code for violation in conflict.pipeline_result.violations})
        self.assertEqual("PUBLICATION_BLOCKED", draft.status)
        self.assertIn("PR_STILL_DRAFT", {violation.code for violation in draft.pipeline_result.violations})

    def test_publication_refresh_retry_exhaustion_blocks_without_merge(self):
        with self.project() as fixture:
            self.create_review_approved_run(fixture, "Unresolved mergeability", 1)
            publisher = FakePublisher(fixture.repo, initial_mergeable=False, refresh_mergeable_sequence=[False, False, False])
            result = RunService(pipeline=RunPipeline(publisher=publisher)).run(RunRequest(fixture.repo, "Unresolved mergeability", 1, fixture.config))

        self.assertEqual("PUBLICATION_BLOCKED", result.status)
        self.assertIn("PR_MERGEABILITY_UNRESOLVED", {violation.code for violation in result.pipeline_result.violations})
        self.assertNotIn("merge", publisher.calls)

    def test_idempotent_merge_retry_resumes_cleanup_without_merging_again(self):
        with self.project() as fixture:
            self.create_review_approved_run(fixture, "Retry merged cleanup", 1)
            publisher = FakePublisher(fixture.repo)
            blocker = PipelineViolation("PRIMARY_FETCH_FAILED", "primary fetch failed after merge", {"stderr": "blocked"})
            with mock.patch("ados.run_pipeline._update_primary_main", side_effect=[(blocker,), ()]):
                first = RunService(pipeline=RunPipeline(publisher=publisher)).run(RunRequest(fixture.repo, "Retry merged cleanup", 1, fixture.config))
                second = RunService(pipeline=RunPipeline(publisher=publisher)).run(RunRequest(fixture.repo, "Retry merged cleanup", 1, fixture.config))

        self.assertEqual("CLEANUP_INCOMPLETE", first.status)
        self.assertTrue(second.resumed)
        self.assertEqual("COMPLETE", second.status)
        self.assertEqual(1, publisher.calls.count("merge"))

    def test_remote_branch_delete_failure_does_not_report_complete(self):
        with self.project() as fixture:
            result = RunService(pipeline=RunPipeline(publisher=FakePublisher(fixture.repo, remote_delete_failure=True))).run(RunRequest(fixture.repo, "Cleanup fails", None, fixture.config))

        self.assertEqual("CLEANUP_INCOMPLETE", result.status)
        self.assertIn("REMOTE_BRANCH_DELETE_FAILED", {violation.code for violation in result.pipeline_result.violations})
        self.assertEqual("1", result.pipeline_result.run_record["pullRequest"])
        self.assertTrue(result.pipeline_result.run_record["mergeCommitSha"])

    def test_cleanup_incomplete_resumes_after_main_moves(self):
        with self.project() as fixture:
            first = RunService(pipeline=RunPipeline(publisher=FakePublisher(fixture.repo, remote_delete_failure=True))).run(RunRequest(fixture.repo, "Retry cleanup", None, fixture.config))
            worktree_path = Path(first.plan.feature_worktree)
            second_publisher = FakePublisher(fixture.repo)
            second = RunService(pipeline=RunPipeline(publisher=second_publisher)).run(RunRequest(fixture.repo, "Retry cleanup", None, fixture.config))
            primary_record = json.loads((fixture.repo / ".agent-workflow" / "runs" / "001-retry-cleanup" / "ados-run.json").read_text(encoding="utf-8"))
            worktree_exists_after_resume = worktree_path.exists()

        self.assertEqual("CLEANUP_INCOMPLETE", first.status)
        self.assertTrue(second.resumed)
        self.assertEqual("COMPLETE", second.status)
        self.assertEqual("COMPLETE", primary_record["status"])
        self.assertEqual("1", primary_record["pullRequest"])
        self.assertTrue(primary_record["mergeCommitSha"])
        self.assertFalse(worktree_exists_after_resume)
        self.assertIn("delete_remote", second_publisher.calls)

    def test_dry_run_still_has_zero_mutations_with_pipeline_available(self):
        with self.project() as fixture:
            before = self.snapshot(fixture.repo)
            result = RunService(pipeline=RunPipeline(publisher=FakePublisher(fixture.repo))).run(RunRequest(fixture.repo, "Plan only full", None, fixture.config, dry_run=True))
            after = self.snapshot(fixture.repo)

        self.assertEqual("PLANNED", result.status)
        self.assertIsNone(result.pipeline_result)
        self.assertEqual(before, after)

    def test_ready_for_publication_resume_does_not_rerun_review(self):
        with self.project() as fixture:
            reviewer = fixture.root / "reviewer.py"
            counter = fixture.root / "review-count.txt"
            reviewer.write_text(
                "from pathlib import Path\n"
                f"counter = Path(r'{counter}')\n"
                "value = int(counter.read_text(encoding='utf-8')) if counter.exists() else 0\n"
                "counter.write_text(str(value + 1), encoding='utf-8')\n"
                "print('Approved')\n",
                encoding="utf-8",
            )
            fixture.config = self.write_config(fixture.root / "project-config.json", fixture.repo, reviewer=f'"{sys.executable}" "{reviewer}"')
            first = RunService().run(RunRequest(fixture.repo, "No duplicate review", None, fixture.config))
            second = RunService().run(RunRequest(fixture.repo, "No duplicate review", None, fixture.config))
            review_count = counter.read_text(encoding="utf-8")

        self.assertEqual("READY_FOR_PUBLICATION", first.status)
        self.assertEqual("READY_FOR_PUBLICATION", second.status)
        self.assertTrue(second.resumed)
        self.assertEqual("1", review_count)

    def test_approved_review_artifact_is_archived_outside_product_source(self):
        with self.project() as fixture:
            reviewer = fixture.root / "reviewer.py"
            reviewer.write_text(
                "from pathlib import Path\n"
                "Path('specs/001-review-artifact/review.md').write_text('Approved review evidence', encoding='utf-8')\n"
                "print('Approved')\n",
                encoding="utf-8",
            )
            implementer = fixture.root / "implementer.py"
            implementer.write_text(
                "from pathlib import Path\n"
                "Path('specs/001-review-artifact').mkdir(parents=True, exist_ok=True)\n"
                "Path('specs/001-review-artifact/spec.md').write_text('# Review artifact\\n', encoding='utf-8')\n",
                encoding="utf-8",
            )
            fixture.config = self.write_config(
                fixture.root / "project-config.json",
                fixture.repo,
                implementer=f'"{sys.executable}" "{implementer}"',
                reviewer=f'"{sys.executable}" "{reviewer}"',
            )
            result = RunService(pipeline=RunPipeline(publisher=FakePublisher(fixture.repo))).run(RunRequest(fixture.repo, "Review artifact", 1, fixture.config))
            archive = Path(result.pipeline_result.run_record["primaryRepository"]) / ".agent-workflow" / "runs" / "001-review-artifact" / "ados-review-evidence.json"
            generated_archive = archive.with_name("review-generated-artifacts.json")
            archive_exists = archive.exists()
            generated_archive_exists = generated_archive.exists()
            generated_payload = json.loads(generated_archive.read_text(encoding="utf-8"))
            generated_content = Path(generated_payload["artifacts"][0]["archive"]).read_text(encoding="utf-8")

        self.assertEqual("COMPLETE", result.status)
        self.assertFalse((Path(result.plan.feature_worktree) / "specs" / "001-review-artifact" / "review.md").exists())
        self.assertTrue(archive_exists)
        self.assertTrue(generated_archive_exists)
        self.assertEqual("archived", generated_payload["artifacts"][0]["status"])
        self.assertEqual("Approved review evidence", generated_content)

    def test_changes_requested_review_side_effect_reason_is_not_resumable(self):
        with self.project(implementer_mode="count") as fixture:
            record_path, record, _reviewed_candidate = self.create_review_changes_requested_blocked_run(fixture, "Review side effect recovery", 1)
            record["reviewBlock"]["reasonCode"] = "REVIEW_SIDE_EFFECT_UNEXPECTED"
            record["reviewBlock"]["reasonCodes"] = ["REVIEW_SIDE_EFFECT_UNEXPECTED"]
            record["reviewBlock"]["blockCause"] = "review_side_effect"
            record_path.write_text(json.dumps(record, indent=2, sort_keys=True), encoding="utf-8")
            worktree = Path(record["featureWorktree"])
            (worktree / "manual-review-fix.txt").write_text("fixed\n", encoding="utf-8")
            self.git(worktree, "add", "manual-review-fix.txt")
            self.git(worktree, "commit", "-m", "manual review recovery")
            status = StatusService().run(StatusRequest(fixture.repo, fixture.config))
            result = RunService(pipeline=RunPipeline(publisher=FakePublisher(fixture.repo))).run(RunRequest(fixture.repo, "Review side effect recovery", 1, fixture.config, dry_run=True))

        self.assertEqual("False", status.workflow.evidence["resumable"])
        self.assertEqual("REVIEW_SIDE_EFFECT_UNEXPECTED", status.workflow.evidence["review_block_reason"])
        self.assertEqual("", status.workflow.evidence["resume_stage"])
        self.assertEqual("BLOCKED", result.status)
        self.assertFalse(result.resumed)
        self.assertIn("ACTIVE_WORKTREE_PRESENT", self.codes(result))

    def test_changes_requested_reason_without_explicit_cause_is_not_resumable(self):
        with self.project(implementer_mode="count") as fixture:
            record_path, record, _reviewed_candidate = self.create_review_changes_requested_blocked_run(fixture, "Missing cause review recovery", 1)
            record["reviewBlock"]["reasonCode"] = "REVIEW_CHANGES_REQUESTED"
            record["reviewBlock"].pop("blockCause", None)
            record_path.write_text(json.dumps(record, indent=2, sort_keys=True), encoding="utf-8")
            worktree = Path(record["featureWorktree"])
            (worktree / "manual-review-fix.txt").write_text("fixed\n", encoding="utf-8")
            self.git(worktree, "add", "manual-review-fix.txt")
            self.git(worktree, "commit", "-m", "manual review recovery")
            status = StatusService().run(StatusRequest(fixture.repo, fixture.config))
            result = RunService(pipeline=RunPipeline(publisher=FakePublisher(fixture.repo))).run(RunRequest(fixture.repo, "Missing cause review recovery", 1, fixture.config, dry_run=True))

        self.assertEqual("False", status.workflow.evidence["resumable"])
        self.assertEqual("REVIEW_CHANGES_REQUESTED", status.workflow.evidence["review_block_reason"])
        self.assertEqual("", status.workflow.evidence["resume_stage"])
        self.assertEqual("BLOCKED", result.status)
        self.assertFalse(result.resumed)
        self.assertIn("ACTIVE_WORKTREE_PRESENT", self.codes(result))

    def test_legacy_unclassified_changes_requested_block_is_not_resumable(self):
        with self.project(implementer_mode="count") as fixture:
            record_path, record, _reviewed_candidate = self.create_review_changes_requested_blocked_run(fixture, "Legacy unclassified review recovery", 1)
            record["reviewBlock"]["reasonCode"] = "REVIEW_BLOCK_UNCLASSIFIED"
            record["reviewBlock"]["resumeStage"] = ""
            record["reviewBlock"].pop("blockCause", None)
            record_path.write_text(json.dumps(record, indent=2, sort_keys=True), encoding="utf-8")
            worktree = Path(record["featureWorktree"])
            (worktree / "manual-review-fix.txt").write_text("fixed\n", encoding="utf-8")
            self.git(worktree, "add", "manual-review-fix.txt")
            self.git(worktree, "commit", "-m", "manual review recovery")
            status = StatusService().run(StatusRequest(fixture.repo, fixture.config))
            result = RunService(pipeline=RunPipeline(publisher=FakePublisher(fixture.repo))).run(RunRequest(fixture.repo, "Legacy unclassified review recovery", 1, fixture.config, dry_run=True))

        self.assertEqual("False", status.workflow.evidence["resumable"])
        self.assertEqual("REVIEW_BLOCK_UNCLASSIFIED", status.workflow.evidence["review_block_reason"])
        self.assertEqual("", status.workflow.evidence["resume_stage"])
        self.assertEqual("BLOCKED", result.status)
        self.assertFalse(result.resumed)
        self.assertIn("ACTIVE_WORKTREE_PRESENT", self.codes(result))

    def test_changes_requested_dirty_review_side_effect_is_not_resumable(self):
        with self.project() as fixture:
            reviewer = fixture.root / "reviewer.py"
            reviewer.write_text(
                "from pathlib import Path\n"
                "Path('review-side-effect.txt').write_text('dirty', encoding='utf-8')\n"
                "print('Changes Requested')\n",
                encoding="utf-8",
            )
            fixture.config = self.write_config(
                fixture.root / "project-config.json",
                fixture.repo,
                reviewer=f'"{sys.executable}" "{reviewer}"',
            )
            first = RunService(pipeline=RunPipeline(publisher=FakePublisher(fixture.repo))).run(RunRequest(fixture.repo, "Dirty review side effect", 1, fixture.config))
            worktree = Path(first.run_record.feature_worktree)
            record_path = worktree / ".agent-workflow" / "runs" / "001-dirty-review-side-effect" / "ados-run.json"
            blocked_record = json.loads(record_path.read_text(encoding="utf-8"))
            status = StatusService().run(StatusRequest(fixture.repo, fixture.config))
            second = RunService(pipeline=RunPipeline(publisher=FakePublisher(fixture.repo))).run(RunRequest(fixture.repo, "Dirty review side effect", 1, fixture.config, dry_run=True))

        self.assertEqual("REVIEW_BLOCKED", first.status)
        self.assertEqual("REVIEW_SIDE_EFFECT_DIRTY_WORKTREE", blocked_record["reviewBlock"]["reasonCode"])
        self.assertEqual("review_side_effect", blocked_record["reviewBlock"]["blockCause"])
        self.assertEqual("False", status.workflow.evidence["resumable"])
        self.assertEqual("", status.workflow.evidence["resume_stage"])
        self.assertEqual("BLOCKED", second.status)
        self.assertFalse(second.resumed)
        self.assertIn("ACTIVE_WORKTREE_PRESENT", self.codes(second))

    def test_cleanup_resume_retries_primary_update_before_complete(self):
        with self.project() as fixture:
            blocker = PipelineViolation("PRIMARY_FETCH_FAILED", "primary fetch failed after merge", {"stderr": "blocked"})
            with mock.patch("ados.run_pipeline._update_primary_main", side_effect=[(blocker,), ()]):
                first = RunService(pipeline=RunPipeline(publisher=FakePublisher(fixture.repo))).run(RunRequest(fixture.repo, "Retry primary update", None, fixture.config))
                second = RunService(pipeline=RunPipeline(publisher=FakePublisher(fixture.repo))).run(RunRequest(fixture.repo, "Retry primary update", None, fixture.config))

        self.assertEqual("CLEANUP_INCOMPLETE", first.status)
        self.assertTrue(second.resumed)
        self.assertEqual("COMPLETE", second.status)

    def test_cleanup_resume_archives_generated_review_artifact_before_worktree_removal(self):
        with self.project() as fixture:
            reviewer = fixture.root / "reviewer.py"
            reviewer.write_text(
                "from pathlib import Path\n"
                "Path('specs/001-cleanup-artifact/review.md').write_text('Approved cleanup evidence', encoding='utf-8')\n"
                "print('Approved')\n",
                encoding="utf-8",
            )
            implementer = fixture.root / "custom-implementer.py"
            implementer.write_text(
                "from pathlib import Path\n"
                "Path('specs/001-cleanup-artifact').mkdir(parents=True, exist_ok=True)\n"
                "Path('specs/001-cleanup-artifact/spec.md').write_text('# Cleanup artifact\\n', encoding='utf-8')\n",
                encoding="utf-8",
            )
            fixture.config = self.write_config(
                fixture.root / "project-config.json",
                fixture.repo,
                implementer=f'"{sys.executable}" "{implementer}"',
                reviewer=f'"{sys.executable}" "{reviewer}"',
            )
            blocker = PipelineViolation("PRIMARY_FETCH_FAILED", "primary fetch failed after merge", {"stderr": "blocked"})
            with mock.patch("ados.run_pipeline._update_primary_main", side_effect=[(blocker,), ()]):
                first = RunService(pipeline=RunPipeline(publisher=FakePublisher(fixture.repo))).run(RunRequest(fixture.repo, "Cleanup artifact", 1, fixture.config))
                worktree_path = Path(first.plan.feature_worktree)
                second = RunService(pipeline=RunPipeline(publisher=FakePublisher(fixture.repo))).run(RunRequest(fixture.repo, "Cleanup artifact", 1, fixture.config))
            generated_archive = fixture.repo / ".agent-workflow" / "runs" / "001-cleanup-artifact" / "review-generated-artifacts.json"
            generated_payload = json.loads(generated_archive.read_text(encoding="utf-8"))
            generated_content = Path(generated_payload["artifacts"][0]["archive"]).read_text(encoding="utf-8")

        self.assertEqual("CLEANUP_INCOMPLETE", first.status)
        self.assertTrue(second.resumed)
        self.assertEqual("COMPLETE", second.status)
        self.assertFalse(worktree_path.exists())
        self.assertEqual("archived", generated_payload["artifacts"][0]["status"])
        self.assertEqual("Approved cleanup evidence", generated_content)

    def test_cleanup_resume_remote_delete_failure_returns_canonical_record(self):
        with self.project() as fixture:
            blocker = PipelineViolation("PRIMARY_FETCH_FAILED", "primary fetch failed after merge", {"stderr": "blocked"})
            with mock.patch("ados.run_pipeline._update_primary_main", return_value=(blocker,)):
                first = RunService(pipeline=RunPipeline(publisher=FakePublisher(fixture.repo))).run(RunRequest(fixture.repo, "Canonical cleanup", None, fixture.config))
            with mock.patch("ados.run_pipeline._update_primary_main", return_value=()):
                second = RunService(pipeline=RunPipeline(publisher=FakePublisher(fixture.repo, remote_delete_failure=True))).run(RunRequest(fixture.repo, "Canonical cleanup", None, fixture.config))
            primary_record_path = fixture.repo / ".agent-workflow" / "runs" / "001-canonical-cleanup" / "ados-run.json"
            primary_record = json.loads(primary_record_path.read_text(encoding="utf-8"))

        self.assertEqual("CLEANUP_INCOMPLETE", first.status)
        self.assertTrue(second.resumed)
        self.assertEqual("CLEANUP_INCOMPLETE", second.status)
        self.assertEqual(primary_record, second.pipeline_result.run_record)

    def test_local_branch_delete_failure_does_not_recreate_removed_worktree(self):
        with self.project() as fixture:
            original_run = run_pipeline._run

            def fail_branch_delete(args, cwd):
                if args[:3] == ("git", "branch", "-d"):
                    return subprocess.CompletedProcess(args, 1, "", "branch delete failed")
                return original_run(args, cwd)

            with mock.patch("ados.run_pipeline._run", side_effect=fail_branch_delete):
                result = RunService(pipeline=RunPipeline(publisher=FakePublisher(fixture.repo))).run(RunRequest(fixture.repo, "Squash cleanup", None, fixture.config))
            primary_record = json.loads((fixture.repo / ".agent-workflow" / "runs" / "001-squash-cleanup" / "ados-run.json").read_text(encoding="utf-8"))
            worktree_exists_after_failure = Path(result.plan.feature_worktree).exists()

        self.assertEqual("CLEANUP_INCOMPLETE", result.status)
        self.assertEqual("CLEANUP_INCOMPLETE", primary_record["status"])
        self.assertFalse(worktree_exists_after_failure)
        self.assertIn("LOCAL_BRANCH_DELETE_FAILED", {violation.code for violation in result.pipeline_result.violations})

    def test_cleanup_resume_after_worktree_removed_uses_existing_archive(self):
        with self.project() as fixture:
            original_run = run_pipeline._run
            fail_delete = {"active": True}

            def fail_branch_delete_once(args, cwd):
                if fail_delete["active"] and args[:3] == ("git", "branch", "-d"):
                    return subprocess.CompletedProcess(args, 1, "", "branch delete failed")
                return original_run(args, cwd)

            with mock.patch("ados.run_pipeline._run", side_effect=fail_branch_delete_once):
                first = RunService(pipeline=RunPipeline(publisher=FakePublisher(fixture.repo))).run(RunRequest(fixture.repo, "Resume removed worktree", None, fixture.config))
            worktree_exists_after_failure = Path(first.plan.feature_worktree).exists()
            archive = fixture.repo / ".agent-workflow" / "runs" / "001-resume-removed-worktree" / "ados-review-evidence.json"
            archive_exists = archive.exists()
            fail_delete["active"] = False
            with mock.patch("ados.run_pipeline._run", side_effect=fail_branch_delete_once):
                second = RunService(pipeline=RunPipeline(publisher=FakePublisher(fixture.repo))).run(RunRequest(fixture.repo, "Resume removed worktree", None, fixture.config))

        self.assertEqual("CLEANUP_INCOMPLETE", first.status)
        self.assertFalse(worktree_exists_after_failure)
        self.assertTrue(archive_exists)
        self.assertTrue(second.resumed)
        self.assertEqual("COMPLETE", second.status)

    def run_cli(self, *args):
        stream = io.StringIO()
        with contextlib.redirect_stdout(stream):
            code = main(list(args))
        return code, stream.getvalue()

    def project(self, **kwargs):
        return TemporaryProject(self, **kwargs)

    def init_repo(self, repo):
        repo.mkdir(parents=True)
        self.git(repo, "init", "-b", "main")
        self.git(repo, "config", "user.email", "test@example.invalid")
        self.git(repo, "config", "user.name", "Test User")
        (repo / ".gitignore").write_text(".agent-workflow/\n", encoding="utf-8")
        (repo / "README.md").write_text("base\n", encoding="utf-8")
        self.git(repo, "add", ".gitignore", "README.md")
        self.git(repo, "commit", "-m", "initial")
        self.git(repo, "remote", "add", "origin", str(repo))
        self.git(repo, "update-ref", "refs/remotes/origin/main", self.head(repo))

    def write_config(self, path, repo, *, project_id="example-project", allowed_paths=(), implementer=None, implementer_mode="success", reviewer=None, bootstrap_commands=None, validation_commands=None):
        if implementer is None:
            runner = path.parent / "implementer.py"
            scripts = {
                "success": (
                    "from pathlib import Path\n"
                    "import os, sys\n"
                    "Path('implementation.txt').write_text('implemented', encoding='utf-8')\n"
                    "print(os.getcwd())\n"
                    "print(sys.stdin.read()[:200])\n"
                ),
                "failure": "import sys\nprint('failed', file=sys.stderr)\nsys.exit(9)\n",
                "timeout": "import time\ntime.sleep(5)\n",
                "count": (
                    "from pathlib import Path\n"
                    f"counter = Path(r'{path.parent / 'implementer-count.txt'}')\n"
                    "value = int(counter.read_text(encoding='utf-8')) if counter.exists() else 0\n"
                    "counter.write_text(str(value + 1), encoding='utf-8')\n"
                    "Path('implementation.txt').write_text('implemented', encoding='utf-8')\n"
                ),
                "no_change": "print('already satisfied')\n",
            }
            runner.write_text(scripts[implementer_mode], encoding="utf-8")
            implementer = f'"{sys.executable}" "{runner}"'
        if reviewer is None:
            reviewer_runner = path.parent / "reviewer.py"
            reviewer_runner.write_text("print('Approved')\n", encoding="utf-8")
            reviewer = f'"{sys.executable}" "{reviewer_runner}"'
        config = {
            "project": {
                "id": project_id,
                "primary_repository_path": str(repo),
                "default_branch": "main",
                "allowed_primary_local_paths": list(allowed_paths),
            },
            "roles": {"implementer": implementer, "reviewer": reviewer},
            "bootstrap": {"commands": list(bootstrap_commands or [])},
            "execution_policy": {
                "schema_version": "1",
                "publication": {"merge_strategy": "merge"},
                "review": {"reviewer": reviewer, "max_rounds": 5},
                "cleanup": {"autonomous": True},
                "guardian": {"stop_on_uncertain": True},
                "validation": {"commands": list(validation_commands or ["git diff --check"])},
            },
        }
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(config), encoding="utf-8")
        return path

    def write_archive(self, repo, *, spec="000-old", validated_sha="", approved_review_sha="", decision="Approved", merge_commit=""):
        archive = repo / ".agent-workflow" / "runs" / spec / "ados-review-evidence.json"
        archive.parent.mkdir(parents=True, exist_ok=True)
        archive.write_text(
            json.dumps(
                {
                    "spec": spec,
                    "validated_sha": validated_sha,
                    "approved_review_sha": approved_review_sha,
                    "claude_decision": decision,
                    "merge_commit": merge_commit,
                }
            ),
            encoding="utf-8",
        )

    def read_run_record(self, worktree, run_dir):
        return json.loads((worktree / ".agent-workflow" / "runs" / run_dir / "ados-run.json").read_text(encoding="utf-8"))

    def codes(self, result):
        return {violation.code for violation in result.eligibility.violations}

    def git(self, repo, *args):
        return subprocess.run(("git", *args), cwd=repo, check=True, capture_output=True, text=True)

    def head(self, repo):
        return self.git(repo, "rev-parse", "HEAD").stdout.strip()

    def snapshot(self, repo):
        return {
            "head": self.head(repo),
            "status": self.git(repo, "status", "--short").stdout,
            "worktrees": self.git(repo, "worktree", "list", "--porcelain").stdout,
        }

    def create_durable_run(self, fixture, feature, spec, status):
        service = RunService()
        config = load_project_config(fixture.config)
        plan = service._plan(fixture.repo, config, RunRequest(fixture.repo, feature, spec, fixture.config, dry_run=True))
        record_model = service._record(config, RunRequest(fixture.repo, feature, spec, fixture.config, dry_run=True), plan)
        worktree = Path(record_model.feature_worktree)
        self.git(fixture.repo, "worktree", "add", "-b", record_model.feature_branch, str(worktree), record_model.authoritative_base_sha)
        record = record_model.to_dict()
        record["status"] = status
        record["nextStage"] = "implementation_handoff" if status == "READY_FOR_IMPLEMENTATION" else "implementation_recovery"
        record_path = worktree / ".agent-workflow" / "runs" / f"{record['specNumber']}-{record['featureSlug']}" / "ados-run.json"
        record_path.parent.mkdir(parents=True)
        record_path.write_text(json.dumps(record, indent=2, sort_keys=True), encoding="utf-8")
        return record_path, record

    def create_review_changes_requested_blocked_run(self, fixture, feature, spec):
        record_path, record = self.create_durable_run(fixture, feature, spec, "REVIEW_BLOCKED")
        worktree = Path(record["featureWorktree"])
        spec_dir = worktree / "specs" / f"{record['specNumber']}-{record['featureSlug']}"
        spec_dir.mkdir(parents=True)
        (spec_dir / "spec.md").write_text(f"# {feature}\n", encoding="utf-8")
        (worktree / "implementation.txt").write_text("implemented\n", encoding="utf-8")
        self.git(worktree, "add", "specs", "implementation.txt")
        self.git(worktree, "commit", "-m", f"spec {record['specNumber']}: {feature}")
        candidate_sha = self.head(worktree)
        record["status"] = "REVIEW_BLOCKED"
        record["nextStage"] = "recovery"
        record["reviewBlock"] = {
            "status": "PASS",
            "decision": "Changes Requested",
            "reasonCode": "REVIEW_CHANGES_REQUESTED",
            "reasonCodes": [],
            "blockCause": "review_decision",
            "transient": False,
            "resumeStage": "implementation_recovery",
            "reviewer": record["reviewer"],
            "candidateSha": candidate_sha,
            "validatedSha": candidate_sha,
            "baseSha": record["authoritativeBaseSha"],
            "reviewedSha": candidate_sha,
            "exitCode": 0,
            "timedOut": False,
        }
        record_path.write_text(json.dumps(record, indent=2, sort_keys=True), encoding="utf-8")
        record_path.with_name("candidate.json").write_text(
            json.dumps({"status": "COMMITTED", "candidate_sha": candidate_sha, "changed_files": [f"specs/{record['specNumber']}-{record['featureSlug']}/spec.md", "implementation.txt"]}, indent=2, sort_keys=True),
            encoding="utf-8",
        )
        record_path.with_name("validation-runtime.json").write_text(
            json.dumps({"status": "PASS", "head_before": candidate_sha, "head_after": candidate_sha, "commands": [], "violations": []}, indent=2, sort_keys=True),
            encoding="utf-8",
        )
        record_path.with_name("review-runtime.json").write_text(
            json.dumps({"status": "PASS", "decision": "Changes Requested", "reviewed_sha": candidate_sha, "exit_code": 0, "stdout": "Changes Requested", "stderr": "", "violations": []}, indent=2, sort_keys=True),
            encoding="utf-8",
        )
        return record_path, record, candidate_sha

    def create_review_approved_run(self, fixture, feature, spec):
        record_path, record = self.create_durable_run(fixture, feature, spec, "REVIEW_APPROVED")
        worktree = Path(record["featureWorktree"])
        spec_dir = worktree / "specs" / f"{record['specNumber']}-{record['featureSlug']}"
        spec_dir.mkdir(parents=True)
        (spec_dir / "spec.md").write_text(f"# {feature}\n", encoding="utf-8")
        self.git(worktree, "add", "specs")
        self.git(worktree, "commit", "-m", f"spec {record['specNumber']}: {feature}")
        candidate_sha = self.head(worktree)
        record["status"] = "REVIEW_APPROVED"
        record["nextStage"] = "publication"
        record_path.write_text(json.dumps(record, indent=2, sort_keys=True), encoding="utf-8")
        (record_path.with_name("candidate.json")).write_text(
            json.dumps({"status": "COMMITTED", "candidate_sha": candidate_sha, "changed_files": [f"specs/{record['specNumber']}-{record['featureSlug']}/spec.md"]}, indent=2, sort_keys=True),
            encoding="utf-8",
        )
        (record_path.with_name("validation-runtime.json")).write_text(
            json.dumps({"status": "PASS", "head_before": candidate_sha, "head_after": candidate_sha, "commands": [], "violations": []}, indent=2, sort_keys=True),
            encoding="utf-8",
        )
        (record_path.with_name("review-runtime.json")).write_text(
            json.dumps({"status": "PASS", "decision": "Approved", "reviewed_sha": candidate_sha, "exit_code": 0, "stdout": "Approved", "stderr": "", "violations": []}, indent=2, sort_keys=True),
            encoding="utf-8",
        )
        return record_path, record


class TemporaryProject:
    def __init__(self, test, **kwargs):
        self.test = test
        self.kwargs = kwargs

    def __enter__(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.repo = self.root / "project"
        specs = self.kwargs.pop("specs", ())
        self.test.init_repo(self.repo)
        for number in specs:
            spec_dir = self.repo / "specs" / f"{number:03d}-example"
            spec_dir.mkdir(parents=True, exist_ok=True)
            (spec_dir / "spec.md").write_text("# Spec\n", encoding="utf-8")
        if specs:
            self.test.git(self.repo, "add", "specs")
            self.test.git(self.repo, "commit", "-m", "add specs")
            self.test.git(self.repo, "update-ref", "refs/remotes/origin/main", self.test.head(self.repo))
        self.config = self.test.write_config(self.root / "project-config.json", self.repo, **self.kwargs)
        return self

    def __exit__(self, exc_type, exc, traceback):
        self.temp.cleanup()


class FakePublisher:
    def __init__(
        self,
        primary,
        *,
        remote_drift=False,
        remote_delete_failure=False,
        ready_failure_once=False,
        initial_mergeable=True,
        refresh_mergeable_sequence=None,
        refresh_merge_state_sequence=None,
        refresh_head_sha=None,
        refresh_base=None,
        refresh_draft=False,
    ):
        self.primary = Path(primary)
        self.remote_drift = remote_drift
        self.remote_delete_failure = remote_delete_failure
        self.ready_failure_once = ready_failure_once
        self.initial_mergeable = initial_mergeable
        self.refresh_mergeable_sequence = list(refresh_mergeable_sequence or [])
        self.refresh_merge_state_sequence = list(refresh_merge_state_sequence or [])
        self.refresh_head_sha = refresh_head_sha
        self.refresh_base = refresh_base
        self.refresh_draft = refresh_draft
        self.calls = []
        self.pr = None
        self.created_pr_count = 0

    def push(self, repo, branch):
        self.calls.append("push")
        return None

    def remote_head(self, repo, branch):
        if self.remote_drift:
            return "0" * 40
        return subprocess.run(("git", "rev-parse", "HEAD"), cwd=repo, check=True, capture_output=True, text=True).stdout.strip()

    def create_or_get_draft_pr(self, repo, *, base, head, title, body):
        self.calls.append("create_pr")
        if self.pr is not None:
            return self.pr
        head_sha = self.remote_head(repo, head)
        self.pr = PullRequestInfo("1", "https://example.invalid/pr/1", base, head, head_sha, self.initial_mergeable, True, base_sha=self.head(self.primary), merge_state_status="CLEAN" if self.initial_mergeable else "UNKNOWN")
        self.created_pr_count += 1
        return self.pr

    def mark_ready(self, repo, number):
        self.calls.append("ready")
        if self.ready_failure_once:
            self.ready_failure_once = False
            from ados.run_pipeline import PipelineViolation

            return PipelineViolation("PR_READY_FAILED", "marking PR ready failed", {"number": number})
        if self.pr is not None:
            self.pr = PullRequestInfo(self.pr.number, self.pr.url, self.pr.base_branch, self.pr.head_branch, self.pr.head_sha, self.pr.mergeable, False, self.pr.base_sha, self.pr.merge_state_status)
        return None

    def refresh_pr(self, repo, number):
        self.calls.append("refresh_pr")
        if self.pr is None:
            return PipelineViolation("PR_REFRESH_FAILED", "PR refresh failed", {"number": number})
        mergeable = self.pr.mergeable
        if self.refresh_mergeable_sequence:
            mergeable = self.refresh_mergeable_sequence.pop(0)
        merge_state = "CLEAN" if mergeable else "UNKNOWN"
        if self.refresh_merge_state_sequence:
            merge_state = self.refresh_merge_state_sequence.pop(0)
        head_sha = self.refresh_head_sha or self.pr.head_sha
        base_branch = self.refresh_base or self.pr.base_branch
        self.pr = PullRequestInfo(
            self.pr.number,
            self.pr.url,
            base_branch,
            self.pr.head_branch,
            head_sha,
            mergeable,
            self.refresh_draft,
            base_sha=self.head(self.primary),
            merge_state_status=merge_state,
        )
        return self.pr

    def merge(self, repo, number, strategy, subject):
        self.calls.append("merge")
        subprocess.run(("git", "merge", "--no-ff", str(self.pr.head_branch), "-m", subject), cwd=self.primary, check=True, capture_output=True, text=True)
        merge_sha = subprocess.run(("git", "rev-parse", "HEAD"), cwd=self.primary, check=True, capture_output=True, text=True).stdout.strip()
        subprocess.run(("git", "update-ref", "refs/remotes/origin/main", merge_sha), cwd=self.primary, check=True, capture_output=True, text=True)
        from ados.run_pipeline import MergeResult

        return MergeResult("MERGED", merge_sha)

    def delete_remote_branch(self, repo, branch):
        self.calls.append("delete_remote")
        if self.remote_delete_failure:
            from ados.run_pipeline import PipelineViolation

            return PipelineViolation("REMOTE_BRANCH_DELETE_FAILED", "remote branch deletion failed", {"branch": branch})
        return None

    def head(self, repo):
        return subprocess.run(("git", "rev-parse", "HEAD"), cwd=repo, check=True, capture_output=True, text=True).stdout.strip()


class FailingOnceLifecycle:
    def __init__(self):
        self.inner = run_pipeline.WorktreeLifecycleEngine()
        self.remove_calls = 0

    def create(self, *, policy, request):
        return self.inner.create(policy=policy, request=request)

    def remove(self, *, policy, request):
        self.remove_calls += 1
        if self.remove_calls == 1:
            return WorktreeLifecycleResult(
                "remove",
                "BLOCK",
                (WorktreeViolation("TEST_CLEANUP_FAILURE", "synthetic cleanup failure", {"worktree_path": str(request.worktree_path)}),),
                (),
            )
        return self.inner.remove(policy=policy, request=request)

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
from ados.requirements_source import hash_requirements_content, read_durable_requirements_content, read_requirements_file, write_requirements_artifacts
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
        self.assertIn("--requirements-file", completed.stdout)
        self.assertIn("--reopen-implementation-recovery", completed.stdout)
        self.assertIn("--reopen-review-side-effect-recovery", completed.stdout)

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

    def test_requirements_file_is_recorded_and_reaches_implementer_and_reviewer(self):
        with self.project() as fixture:
            requirements = fixture.root / "requirements.md"
            requirements.write_text("runtime rendering is mandatory; metadata-only is insufficient\n", encoding="utf-8")
            implementer_prompt = fixture.root / "implementer-prompt.txt"
            reviewer_prompt = fixture.root / "reviewer-prompt.txt"
            implementer = fixture.root / "requirements-implementer.py"
            reviewer = fixture.root / "requirements-reviewer.py"
            implementer.write_text(
                "from pathlib import Path\n"
                f"prompt_path = Path(r'{implementer_prompt}')\n"
                "prompt = __import__('sys').stdin.read()\n"
                "prompt_path.write_text(prompt, encoding='utf-8')\n"
                "assert 'runtime rendering is mandatory' in prompt\n"
                "spec_dir = Path('specs/001-rendered-office')\n"
                "spec_dir.mkdir(parents=True, exist_ok=True)\n"
                "(spec_dir / 'spec.md').write_text('# Rendered Office\\n\\nruntime rendering is mandatory\\n', encoding='utf-8')\n"
                "Path('implementation.txt').write_text('rendered office candidate', encoding='utf-8')\n",
                encoding="utf-8",
            )
            reviewer.write_text(
                "from pathlib import Path\n"
                f"prompt_path = Path(r'{reviewer_prompt}')\n"
                "prompt = __import__('sys').stdin.read()\n"
                "prompt_path.write_text(prompt, encoding='utf-8')\n"
                "assert 'runtime rendering is mandatory' in prompt\n"
                "assert 'metadata-only is insufficient' in prompt\n"
                "print('Approved')\n",
                encoding="utf-8",
            )
            fixture.config = self.write_config(
                fixture.root / "project-config.json",
                fixture.repo,
                implementer=f'"{sys.executable}" "{implementer}"',
                reviewer=f'"{sys.executable}" "{reviewer}"',
            )

            result = RunService(pipeline=RunPipeline(publisher=FakePublisher(fixture.repo))).run(
                RunRequest(fixture.repo, "Rendered Office", 1, fixture.config, requirements_file=requirements)
            )
            archive_dir = fixture.repo / ".agent-workflow" / "runs" / "001-rendered-office"
            run_record = json.loads((archive_dir / "ados-run.json").read_text(encoding="utf-8"))
            requirements_metadata = json.loads((archive_dir / "requirements-source.json").read_text(encoding="utf-8"))
            requirements_copy = (archive_dir / "requirements-source.md").read_text(encoding="utf-8")
            implementer_prompt_text = implementer_prompt.read_text(encoding="utf-8")
            reviewer_prompt_text = reviewer_prompt.read_text(encoding="utf-8")

        self.assertEqual("COMPLETE", result.status)
        self.assertEqual("true", str(run_record["requirements"]["supplied"]).lower())
        self.assertEqual(run_record["requirements"]["sha256"], requirements_metadata["canonicalSha256"])
        self.assertIn("runtime rendering is mandatory", requirements_copy)
        self.assertIn("BEGIN AUTHORITATIVE REQUIREMENTS", implementer_prompt_text)
        self.assertIn("BEGIN AUTHORITATIVE REQUIREMENTS", reviewer_prompt_text)

    def test_requirements_file_reaches_bootstrap_spec_generation(self):
        with self.project() as fixture:
            requirements = fixture.root / "visual-requirements.md"
            requirements.write_text(
                "The existing visible office composition must be replaced. "
                "Actual runtime Phaser rendering is mandatory. Metadata-only implementation is insufficient.\n",
                encoding="utf-8",
            )
            prompt_path = fixture.root / "bootstrap-prompt.txt"
            bootstrap = fixture.root / "bootstrap-spec.py"
            bootstrap.write_text(
                "from pathlib import Path\n"
                "import sys\n"
                "prompt = sys.stdin.read()\n"
                f"Path(r'{prompt_path}').write_text(prompt, encoding='utf-8')\n"
                "spec_dir = Path('specs/135-rendered-office')\n"
                "spec_dir.mkdir(parents=True, exist_ok=True)\n"
                "if 'Actual runtime Phaser rendering is mandatory' in prompt and 'Metadata-only implementation is insufficient' in prompt:\n"
                "    spec = '# Rendered Office\\n\\nActual runtime Phaser rendering is mandatory. Metadata-only implementation is insufficient.\\n'\n"
                "else:\n"
                "    spec = '# Rendered Office\\n\\nThis feature only adds metadata. Rendering is out of scope.\\n'\n"
                "(spec_dir / 'spec.md').write_text(spec, encoding='utf-8')\n",
                encoding="utf-8",
            )
            fixture.config = self.write_config(fixture.root / "project-config.json", fixture.repo, bootstrap_commands=[f'"{sys.executable}" "{bootstrap}"'])
            source = read_requirements_file(requirements)
            self.assertFalse(hasattr(source, "code"))
            run_dir = fixture.repo / ".agent-workflow" / "runs" / "135-rendered-office"
            run_dir.mkdir(parents=True)
            record_path = run_dir / "ados-run.json"
            record = {
                "projectId": "example-project",
                "featureWorktree": str(fixture.repo),
                "specNumber": "135",
                "featureSlug": "rendered-office",
                "requirements": source.to_record(),
            }
            record_path.write_text(json.dumps(record, indent=2, sort_keys=True), encoding="utf-8")
            write_requirements_artifacts(record_path, record, source)

            result = RunPipeline()._bootstrap(load_project_config(fixture.config), record, record_path)
            prompt = prompt_path.read_text(encoding="utf-8")
            generated_spec = (fixture.repo / "specs" / "135-rendered-office" / "spec.md").read_text(encoding="utf-8")

        self.assertEqual(1, len(result))
        self.assertEqual(0, result[0].exit_code)
        self.assertIn("BEGIN AUTHORITATIVE REQUIREMENTS", prompt)
        self.assertIn("Actual runtime Phaser rendering is mandatory", prompt)
        self.assertIn("Actual runtime Phaser rendering is mandatory", generated_spec)
        self.assertNotIn("Rendering is out of scope", generated_spec)

    def test_resume_uses_durable_requirements_and_changed_external_file_blocks_when_supplied(self):
        with self.project() as fixture:
            requirements = fixture.root / "requirements.md"
            requirements.write_text("original durable requirement\n", encoding="utf-8")
            reviewer_counter = fixture.root / "review-count.txt"
            reviewer_prompt = fixture.root / "reviewer-prompt.txt"
            reviewer = fixture.root / "transient-reviewer.py"
            reviewer.write_text(
                "from pathlib import Path\n"
                "import sys\n"
                f"counter = Path(r'{reviewer_counter}')\n"
                "count = int(counter.read_text(encoding='utf-8')) if counter.exists() else 0\n"
                "counter.write_text(str(count + 1), encoding='utf-8')\n"
                "prompt = sys.stdin.read()\n"
                "if count == 0:\n"
                "    print('temporary outage', file=sys.stderr)\n"
                "    sys.exit(3)\n"
                f"Path(r'{reviewer_prompt}').write_text(prompt, encoding='utf-8')\n"
                "print('Approved')\n",
                encoding="utf-8",
            )
            fixture.config = self.write_config(fixture.root / "project-config.json", fixture.repo, reviewer=f'"{sys.executable}" "{reviewer}"')
            service = RunService(pipeline=RunPipeline(publisher=FakePublisher(fixture.repo, remote_drift=True)))

            first = service.run(RunRequest(fixture.repo, "Durable Requirements Resume", 1, fixture.config, requirements_file=requirements))
            requirements.write_text("changed external requirement\n", encoding="utf-8")
            blocked = service.run(RunRequest(fixture.repo, "Durable Requirements Resume", 1, fixture.config, dry_run=True, requirements_file=requirements))
            run_dir = Path(first.plan.feature_worktree) / ".agent-workflow" / "runs" / "001-durable-requirements-resume"
            durable_record = json.loads((run_dir / "ados-run.json").read_text(encoding="utf-8"))
            durable_requirements = read_durable_requirements_content(run_dir / "ados-run.json", durable_record)

        self.assertEqual("REVIEW_BLOCKED", first.status)
        self.assertEqual("BLOCKED", blocked.status)
        self.assertIn("REQUIREMENTS_FILE_CHANGED", self.codes(blocked))
        self.assertIn("original durable requirement", durable_requirements)
        self.assertNotIn("changed external requirement", durable_requirements)

    def test_missing_and_empty_requirements_files_block_before_worktree_creation(self):
        with self.project() as fixture:
            missing = fixture.root / "missing.md"
            empty = fixture.root / "empty.md"
            empty.write_text("  \n", encoding="utf-8")

            missing_result = RunService().run(RunRequest(fixture.repo, "Missing Requirements", 1, fixture.config, requirements_file=missing))
            empty_result = RunService().run(RunRequest(fixture.repo, "Empty Requirements", 2, fixture.config, requirements_file=empty))

        self.assertEqual("INVALID", missing_result.status)
        self.assertEqual("INVALID", empty_result.status)
        self.assertIn("REQUIREMENTS_FILE_READ_FAILED", self.codes(missing_result))
        self.assertIn("REQUIREMENTS_FILE_EMPTY", self.codes(empty_result))

    def test_requirements_hash_mismatch_blocks_resume(self):
        with tempfile.TemporaryDirectory() as temp:
            run_dir = Path(temp)
            record_path = run_dir / "ados-run.json"
            record = {
                "runId": "requirements-test",
                "requirements": {
                    "supplied": True,
                    "sourcePath": str(run_dir / "requirements.md"),
                    "sha256": hash_requirements_content("stable requirement\n"),
                    "contentArtifact": "requirements-source.md",
                    "metadataArtifact": "requirements-source.json",
                },
            }
            record_path.write_text(json.dumps(record), encoding="utf-8")
            (run_dir / "requirements-source.md").write_text("tampered requirement\n", encoding="utf-8")

            outcome = RunPipeline().run(config=None, run_record_path=record_path, timeout_ms=1000)

        self.assertEqual("BLOCKED", outcome.status)
        self.assertIn("REQUIREMENTS_HASH_MISMATCH", [violation.code for violation in outcome.violations])

    def test_metadata_only_scope_inversion_against_rendering_requirement_is_not_accepted(self):
        with self.project() as fixture:
            requirements = fixture.root / "visual-requirements.md"
            requirements.write_text("runtime rendering is mandatory; metadata-only is insufficient\n", encoding="utf-8")
            implementer = fixture.root / "metadata-only-implementer.py"
            reviewer = fixture.root / "requirements-aware-reviewer.py"
            implementer.write_text(
                "from pathlib import Path\n"
                "spec_dir = Path('specs/001-physical-office')\n"
                "spec_dir.mkdir(parents=True, exist_ok=True)\n"
                "(spec_dir / 'spec.md').write_text('# Physical Office\\n\\nMetadata only; rendering is out of scope.\\n', encoding='utf-8')\n"
                "Path('metadata.txt').write_text('metadata only', encoding='utf-8')\n",
                encoding="utf-8",
            )
            reviewer.write_text(
                "import sys\n"
                "from pathlib import Path\n"
                "prompt = sys.stdin.read()\n"
                "spec = Path('specs/001-physical-office/spec.md').read_text(encoding='utf-8')\n"
                "if 'runtime rendering is mandatory' in prompt and 'rendering is out of scope' in spec:\n"
                "    print('Decision: Changes Requested')\n"
                "    print('Blocking: metadata-only scope contradicts authoritative rendering requirement')\n"
                "else:\n"
                "    print('Approved')\n",
                encoding="utf-8",
            )
            fixture.config = self.write_config(
                fixture.root / "project-config.json",
                fixture.repo,
                implementer=f'"{sys.executable}" "{implementer}"',
                reviewer=f'"{sys.executable}" "{reviewer}"',
            )

            result = RunService().run(RunRequest(fixture.repo, "Physical Office", 1, fixture.config, requirements_file=requirements))

        self.assertEqual("REVIEW_BLOCKED", result.status)
        self.assertEqual("Changes Requested", result.pipeline_result.review.decision)

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
        self.assertIn("ACTIVE_WORKTREE_PRESENT", self.codes(result))

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

    def test_orphaned_candidate_is_adopted_without_implementer_and_receives_fresh_validation_and_review(self):
        with self.project(implementer_mode="count") as fixture:
            feature = "Orphaned candidate adoption"
            plan, worktree, candidate_sha = self.create_orphaned_candidate(fixture, feature, 1)
            publisher = FakePublisher(fixture.repo, remote_drift=True)

            result = RunService(pipeline=RunPipeline(publisher=publisher)).run(RunRequest(fixture.repo, feature, 1, fixture.config))

            run_dir = worktree / ".agent-workflow" / "runs" / f"{plan.spec_number}-{plan.feature_slug}"
            adoption = json.loads((run_dir / "orphaned-candidate-adoption.json").read_text(encoding="utf-8"))
            candidate = json.loads((run_dir / "candidate.json").read_text(encoding="utf-8"))
            validation = json.loads((run_dir / "validation-runtime.json").read_text(encoding="utf-8"))
            review = json.loads((run_dir / "review-runtime.json").read_text(encoding="utf-8"))
            status = StatusService().run(StatusRequest(fixture.repo, fixture.config))
            implementer_called = (fixture.root / "implementer-count.txt").exists()

        stages = [stage.id for stage in result.pipeline_result.stages]
        self.assertTrue(result.adopted)
        self.assertFalse(result.resumed)
        self.assertEqual("PUBLICATION_BLOCKED", result.status)
        self.assertIn("orphaned_candidate_adoption", stages)
        self.assertEqual("SKIPPED", next(stage.status for stage in result.pipeline_result.stages if stage.id == "implementer"))
        self.assertFalse(implementer_called)
        self.assertEqual(candidate_sha, adoption["candidateSha"])
        self.assertFalse(adoption["externalValidationTrusted"])
        self.assertFalse(adoption["externalReviewTrusted"])
        self.assertEqual(candidate_sha, candidate["candidate_sha"])
        self.assertEqual("PASS", validation["status"])
        self.assertEqual(candidate_sha, validation["head_before"])
        self.assertEqual(candidate_sha, validation["head_after"])
        self.assertEqual("Approved", review["decision"])
        self.assertEqual(candidate_sha, review["reviewed_sha"])
        self.assertEqual("Passed", status.validation.state)
        self.assertEqual(candidate_sha, status.validation.evidence["validated_sha"])
        self.assertEqual("Approved", status.review.state)
        self.assertEqual(candidate_sha, status.review.evidence["reviewed_sha"])

    def test_orphaned_candidate_dry_run_is_visible_without_durable_mutation(self):
        with self.project() as fixture:
            feature = "Visible orphan adoption"
            _plan, worktree, _candidate_sha = self.create_orphaned_candidate(fixture, feature, 1)
            before = self.snapshot(fixture.repo)

            result = RunService().run(RunRequest(fixture.repo, feature, 1, fixture.config, dry_run=True))
            after = self.snapshot(fixture.repo)

        self.assertEqual("PLANNED", result.status)
        self.assertTrue(result.adopted)
        self.assertEqual("validation", result.run_record.next_stage)
        self.assertEqual(before, after)
        self.assertFalse((worktree / ".agent-workflow").exists())

    def test_interrupted_orphan_adoption_resumes_from_durable_record_without_implementer(self):
        with self.project(implementer_mode="count") as fixture:
            feature = "Interrupted orphan adoption"
            plan, _worktree, candidate_sha = self.create_orphaned_candidate(fixture, feature, 1)
            service = RunService()
            adoption = service._orphaned_candidate_adoption(fixture.repo, load_project_config(fixture.config), plan, feature)
            record_path = service._persist_orphaned_candidate_adoption(adoption)
            record_path.with_name("orphaned-candidate-adoption.json").unlink()
            record_path.with_name("candidate.json").unlink()

            result = RunService(pipeline=RunPipeline(publisher=FakePublisher(fixture.repo, remote_drift=True))).run(
                RunRequest(fixture.repo, feature, 1, fixture.config)
            )

            candidate = json.loads(record_path.with_name("candidate.json").read_text(encoding="utf-8"))
            validation = json.loads(record_path.with_name("validation-runtime.json").read_text(encoding="utf-8"))
            review = json.loads(record_path.with_name("review-runtime.json").read_text(encoding="utf-8"))
            implementer_called = (fixture.root / "implementer-count.txt").exists()
            adoption_artifact_exists = record_path.with_name("orphaned-candidate-adoption.json").exists()

        self.assertTrue(result.resumed)
        self.assertFalse(result.adopted)
        self.assertIn("orphaned_candidate_adoption", [stage.id for stage in result.pipeline_result.stages])
        self.assertFalse(implementer_called)
        self.assertTrue(adoption_artifact_exists)
        self.assertEqual(candidate_sha, candidate["candidate_sha"])
        self.assertEqual(candidate_sha, validation["head_after"])
        self.assertEqual(candidate_sha, review["reviewed_sha"])

    def test_adopted_candidate_status_does_not_reuse_historical_validation_or_review(self):
        with self.project() as fixture:
            feature = "Isolated orphan evidence"
            plan, _worktree, _candidate_sha = self.create_orphaned_candidate(fixture, feature, 1)
            self.write_archive(
                fixture.repo,
                spec="000-historical",
                validated_sha=plan.authoritative_base_sha,
                approved_review_sha=plan.authoritative_base_sha,
            )
            service = RunService()
            config = load_project_config(fixture.config)
            adoption = service._orphaned_candidate_adoption(fixture.repo, config, plan, feature)
            service._persist_orphaned_candidate_adoption(adoption)

            status = StatusService().run(StatusRequest(fixture.repo, fixture.config))

        self.assertEqual("Unavailable", status.validation.state)
        self.assertEqual("Unavailable", status.review.state)
        self.assertEqual("Unavailable", status.exact_head_gate.state)
        self.assertEqual("001", status.workflow.evidence["run_spec"])

    def test_adopted_candidate_status_never_promotes_failed_or_nonapproved_evidence(self):
        with self.project() as fixture:
            feature = "Safe orphan status evidence"
            plan, worktree, candidate_sha = self.create_orphaned_candidate(fixture, feature, 1)
            service = RunService()
            adoption = service._orphaned_candidate_adoption(fixture.repo, load_project_config(fixture.config), plan, feature)
            record_path = service._persist_orphaned_candidate_adoption(adoption)
            record_path.with_name("validation-runtime.json").write_text(
                json.dumps({"status": "PASS", "head_before": candidate_sha, "head_after": candidate_sha, "commands": [], "violations": []}),
                encoding="utf-8",
            )
            record_path.with_name("review-runtime.json").write_text(
                json.dumps({"status": "PASS", "decision": "Changes Requested", "reviewed_sha": candidate_sha, "exit_code": 0, "stdout": "Changes Requested", "stderr": "", "violations": []}),
                encoding="utf-8",
            )
            changes_requested = StatusService().run(StatusRequest(fixture.repo, fixture.config))
            record_path.with_name("validation-runtime.json").write_text(
                json.dumps({"status": "BLOCK", "head_before": candidate_sha, "head_after": candidate_sha, "commands": [], "violations": []}),
                encoding="utf-8",
            )
            failed_validation = StatusService().run(StatusRequest(fixture.repo, fixture.config))

        self.assertEqual("ChangesRequested", changes_requested.review.state)
        self.assertEqual("Unavailable", changes_requested.exact_head_gate.state)
        self.assertEqual("Unavailable", failed_validation.validation.state)
        self.assertEqual("Unavailable", failed_validation.exact_head_gate.state)

    def test_dirty_orphaned_candidate_is_refused_without_cleanup(self):
        with self.project() as fixture:
            feature = "Dirty orphan adoption"
            _plan, worktree, _candidate_sha = self.create_orphaned_candidate(fixture, feature, 1)
            (worktree / "orphan.txt").write_text("dirty after commit\n", encoding="utf-8")
            before = self.snapshot(worktree)

            result = RunService().run(RunRequest(fixture.repo, feature, 1, fixture.config, dry_run=True))
            after = self.snapshot(worktree)

        self.assertEqual("BLOCKED", result.status)
        self.assertIn("ORPHANED_CANDIDATE_DIRTY", self.codes(result))
        self.assertEqual(before, after)

    def test_orphaned_candidate_wrong_branch_is_refused(self):
        with self.project() as fixture:
            feature = "Wrong branch orphan"
            plan = RunService()._plan(fixture.repo, load_project_config(fixture.config), RunRequest(fixture.repo, feature, 1, fixture.config, dry_run=True))
            self.create_orphaned_candidate(fixture, feature, 1, branch="codex/001-other", path=Path(plan.feature_worktree))
            result = RunService().run(RunRequest(fixture.repo, feature, 1, fixture.config, dry_run=True))

        self.assertEqual("BLOCKED", result.status)
        self.assertIn("ORPHANED_CANDIDATE_BRANCH_MISMATCH", self.codes(result))

    def test_orphaned_candidate_wrong_worktree_path_is_refused(self):
        with self.project() as fixture:
            feature = "Wrong path orphan"
            wrong_path = fixture.root / "wrong-orphan-path"
            self.create_orphaned_candidate(fixture, feature, 1, path=wrong_path)
            result = RunService().run(RunRequest(fixture.repo, feature, 1, fixture.config, dry_run=True))

        self.assertEqual("BLOCKED", result.status)
        self.assertIn("ORPHANED_CANDIDATE_WORKTREE_PATH_MISMATCH", self.codes(result))

    def test_orphaned_candidate_equal_to_base_is_refused(self):
        with self.project() as fixture:
            feature = "Empty orphan candidate"
            self.create_orphaned_candidate(fixture, feature, 1, commit=False)
            result = RunService().run(RunRequest(fixture.repo, feature, 1, fixture.config, dry_run=True))

        self.assertEqual("BLOCKED", result.status)
        self.assertIn("ORPHANED_CANDIDATE_EQUALS_BASE", self.codes(result))

    def test_orphaned_candidate_outside_base_lineage_is_refused(self):
        with self.project() as fixture:
            feature = "Unrelated orphan candidate"
            plan = RunService()._plan(fixture.repo, load_project_config(fixture.config), RunRequest(fixture.repo, feature, 1, fixture.config, dry_run=True))
            worktree = Path(plan.feature_worktree)
            self.git(fixture.repo, "worktree", "add", "--detach", str(worktree), plan.authoritative_base_sha)
            self.git(worktree, "switch", "--orphan", plan.feature_branch)
            (worktree / "README.md").write_text("unrelated root\n", encoding="utf-8")
            self.git(worktree, "add", "-A")
            self.git(worktree, "commit", "-m", "unrelated orphan")
            result = RunService().run(RunRequest(fixture.repo, feature, 1, fixture.config, dry_run=True))

        self.assertEqual("BLOCKED", result.status)
        self.assertIn("ORPHANED_CANDIDATE_LINEAGE_MISMATCH", self.codes(result))

    def test_multiple_spec_worktrees_make_orphaned_candidate_ambiguous(self):
        with self.project() as fixture:
            feature = "Ambiguous orphan candidate"
            self.create_orphaned_candidate(fixture, feature, 1)
            other = fixture.root / "other-spec-one"
            self.git(fixture.repo, "worktree", "add", "-b", "codex/001-other", str(other), "HEAD")
            result = RunService().run(RunRequest(fixture.repo, feature, 1, fixture.config, dry_run=True))

        self.assertEqual("BLOCKED", result.status)
        self.assertIn("ORPHANED_CANDIDATE_AMBIGUOUS", self.codes(result))

    def test_existing_durable_resume_wins_over_orphan_adoption(self):
        with self.project() as fixture:
            record_path, record = self.create_durable_run(fixture, "Durable wins", 1, "READY_FOR_IMPLEMENTATION")
            result = RunService(pipeline=RunPipeline(publisher=FakePublisher(fixture.repo, remote_drift=True))).run(
                RunRequest(fixture.repo, "Durable wins", 1, fixture.config)
            )

        self.assertTrue(result.resumed)
        self.assertFalse(result.adopted)
        self.assertEqual(record["runId"], result.run_record.run_id)
        self.assertFalse(record_path.with_name("orphaned-candidate-adoption.json").exists())

    def test_already_merged_orphaned_candidate_is_refused(self):
        with self.project() as fixture:
            feature = "Merged orphan candidate"
            plan, _worktree, _candidate = self.create_orphaned_candidate(fixture, feature, 1)
            self.git(fixture.repo, "merge", "--no-ff", plan.feature_branch, "-m", "merge orphan")
            self.git(fixture.repo, "update-ref", "refs/remotes/origin/main", self.head(fixture.repo))
            result = RunService().run(RunRequest(fixture.repo, feature, 1, fixture.config, dry_run=True))

        self.assertEqual("BLOCKED", result.status)
        self.assertIn("ORPHANED_CANDIDATE_ALREADY_MERGED", self.codes(result))

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

    def test_implementer_failure_retries_and_continues_to_validation(self):
        with self.project() as fixture:
            counter = fixture.root / "implementer-count.txt"
            implementer = fixture.root / "flaky-implementer.py"
            implementer.write_text(
                "from pathlib import Path\n"
                "import sys\n"
                f"counter = Path(r'{counter}')\n"
                "value = int(counter.read_text(encoding='utf-8')) if counter.exists() else 0\n"
                "counter.write_text(str(value + 1), encoding='utf-8')\n"
                "handoff = sys.stdin.read()\n"
                "if value == 0:\n"
                "    print('temporary implementer failure', file=sys.stderr)\n"
                "    sys.exit(9)\n"
                "assert 'Implementation failure recovery context:' in handoff\n"
                "Path('implementation.txt').write_text('implemented after retry', encoding='utf-8')\n",
                encoding="utf-8",
            )
            fixture.config = self.write_config(fixture.root / "project-config.json", fixture.repo, implementer=f'"{sys.executable}" "{implementer}"')

            result = RunService(pipeline=RunPipeline(publisher=FakePublisher(fixture.repo))).run(RunRequest(fixture.repo, "Flaky implementer", None, fixture.config))
            record = json.loads((fixture.repo / ".agent-workflow" / "runs" / "001-flaky-implementer" / "ados-run.json").read_text(encoding="utf-8"))
            stages = [stage.id for stage in result.pipeline_result.stages]
            implementer_count = counter.read_text(encoding="utf-8")

        self.assertEqual("COMPLETE", result.status)
        self.assertIn("implementation_recovery_implementer", stages)
        self.assertEqual("2", implementer_count)
        self.assertEqual(1, len(record["implementationRecoveryAttempts"]))
        self.assertEqual("IMPLEMENTATION_FAILED", record["implementationRecoveryAttempts"][0]["priorStatus"])
        self.assertEqual(result.pipeline_result.candidate.candidate_sha, result.pipeline_result.validation.head_after)

    def test_implementer_timeout_recovery_records_timeout_evidence(self):
        with self.project() as fixture:
            counter = fixture.root / "implementer-count.txt"
            implementer = fixture.root / "timeout-then-success.py"
            implementer.write_text(
                "from pathlib import Path\n"
                "import sys, time\n"
                f"counter = Path(r'{counter}')\n"
                "value = int(counter.read_text(encoding='utf-8')) if counter.exists() else 0\n"
                "counter.write_text(str(value + 1), encoding='utf-8')\n"
                "if value == 0:\n"
                "    print('before timeout')\n"
                "    time.sleep(5)\n"
                "Path('implementation.txt').write_text('implemented after timeout', encoding='utf-8')\n",
                encoding="utf-8",
            )
            fixture.config = self.write_config(fixture.root / "project-config.json", fixture.repo, implementer=f'"{sys.executable}" "{implementer}"')

            result = RunService(pipeline=RunPipeline(publisher=FakePublisher(fixture.repo))).run(RunRequest(fixture.repo, "Timeout implementer", None, fixture.config, implementer_timeout_ms=100))
            record = json.loads((fixture.repo / ".agent-workflow" / "runs" / "001-timeout-implementer" / "ados-run.json").read_text(encoding="utf-8"))
            attempt = record["implementationRecoveryAttempts"][0]

        self.assertEqual("COMPLETE", result.status)
        self.assertEqual("IMPLEMENTATION_TIMED_OUT", attempt["priorStatus"])
        self.assertEqual("true", attempt["priorTimedOut"])

    def test_repeated_implementer_failure_reaches_configured_bound(self):
        with self.project(implementer_mode="failure", implementation_max_recovery_rounds=1) as fixture:
            publisher = FakePublisher(fixture.repo)
            result = RunService(pipeline=RunPipeline(publisher=publisher)).run(RunRequest(fixture.repo, "Always failing implementer", None, fixture.config))
            record_path = Path(result.run_record.feature_worktree) / ".agent-workflow" / "runs" / "001-always-failing-implementer" / "ados-run.json"
            record = json.loads(record_path.read_text(encoding="utf-8"))

        self.assertEqual("IMPLEMENTATION_FAILED", result.status)
        self.assertEqual([], publisher.calls)
        self.assertEqual(1, len(record["implementationRecoveryAttempts"]))
        self.assertEqual("IMPLEMENTATION_RECOVERY_MAX_ROUNDS_EXCEEDED", record["implementationRecoveryBlock"]["reasonCode"])
        self.assertIsNone(result.pipeline_result.validation)

    def test_exhausted_implementation_recovery_requires_explicit_reopen(self):
        with self.project() as fixture:
            counter = fixture.root / "implementer-count.txt"
            implementer = fixture.root / "fail-twice-then-success.py"
            implementer.write_text(
                "from pathlib import Path\n"
                "import sys\n"
                f"counter = Path(r'{counter}')\n"
                "prompt = sys.stdin.read()\n"
                "value = int(counter.read_text(encoding='utf-8')) if counter.exists() else 0\n"
                "counter.write_text(str(value + 1), encoding='utf-8')\n"
                "if value < 2:\n"
                "    print('temporary implementer failure', file=sys.stderr)\n"
                "    sys.exit(9)\n"
                "assert 'Implementation failure recovery context:' in prompt\n"
                "Path('implementation.txt').write_text('implemented after explicit reopen', encoding='utf-8')\n",
                encoding="utf-8",
            )
            fixture.config = self.write_config(
                fixture.root / "project-config.json",
                fixture.repo,
                implementer=f'"{sys.executable}" "{implementer}"',
                implementation_max_recovery_rounds=1,
                implementation_max_recovery_reopens=1,
            )
            publisher = FakePublisher(fixture.repo)
            service = RunService(pipeline=RunPipeline(publisher=publisher))

            first = service.run(RunRequest(fixture.repo, "Reopen implementation recovery", 1, fixture.config))
            record_path = Path(first.run_record.feature_worktree) / ".agent-workflow" / "runs" / "001-reopen-implementation-recovery" / "ados-run.json"
            first_record = json.loads(record_path.read_text(encoding="utf-8"))
            second = service.run(RunRequest(fixture.repo, "Reopen implementation recovery", 1, fixture.config))
            second_record = json.loads(record_path.read_text(encoding="utf-8"))
            third = service.run(RunRequest(fixture.repo, "Reopen implementation recovery", 1, fixture.config, reopen_implementation_recovery=True))
            final_record = json.loads((fixture.repo / ".agent-workflow" / "runs" / "001-reopen-implementation-recovery" / "ados-run.json").read_text(encoding="utf-8"))
            stages = [stage.id for stage in third.pipeline_result.stages]

        self.assertEqual("IMPLEMENTATION_FAILED", first.status)
        self.assertEqual("IMPLEMENTATION_RECOVERY_MAX_ROUNDS_EXCEEDED", first_record["implementationRecoveryBlock"]["reasonCode"])
        self.assertEqual("IMPLEMENTATION_FAILED", second.status)
        self.assertIn("IMPLEMENTATION_RECOVERY_MAX_ROUNDS_EXCEEDED", {violation.code for violation in second.pipeline_result.violations})
        self.assertEqual(1, len(second_record["implementationRecoveryAttempts"]))
        self.assertEqual("COMPLETE", third.status)
        self.assertTrue(third.resumed)
        self.assertIn("implementation_recovery_reopen", stages)
        self.assertIn("implementation_recovery_implementer", stages)
        self.assertEqual(first_record["runId"], final_record["runId"])
        self.assertEqual(1, len(final_record["implementationRecoveryReopens"]))
        self.assertEqual(2, len(final_record["implementationRecoveryAttempts"]))
        self.assertEqual([0, 1], [attempt.get("reopenEpoch", 0) for attempt in final_record["implementationRecoveryAttempts"]])
        self.assertEqual(third.pipeline_result.candidate.candidate_sha, third.pipeline_result.validation.head_after)
        self.assertEqual(third.pipeline_result.validation.head_after, third.pipeline_result.review.reviewed_sha)

    def test_exhausted_implementation_recovery_reopen_is_bounded(self):
        with self.project(implementation_max_recovery_rounds=1, implementation_max_recovery_reopens=1, implementer_mode="failure") as fixture:
            service = RunService(pipeline=RunPipeline(publisher=FakePublisher(fixture.repo)))
            first = service.run(RunRequest(fixture.repo, "Bounded implementation reopen", 1, fixture.config))
            second = service.run(RunRequest(fixture.repo, "Bounded implementation reopen", 1, fixture.config, reopen_implementation_recovery=True))
            third = service.run(RunRequest(fixture.repo, "Bounded implementation reopen", 1, fixture.config, reopen_implementation_recovery=True))
            record_path = Path(first.run_record.feature_worktree) / ".agent-workflow" / "runs" / "001-bounded-implementation-reopen" / "ados-run.json"
            record = json.loads(record_path.read_text(encoding="utf-8"))

        self.assertEqual("IMPLEMENTATION_FAILED", first.status)
        self.assertEqual("IMPLEMENTATION_FAILED", second.status)
        self.assertEqual("IMPLEMENTATION_FAILED", third.status)
        self.assertEqual(1, len(record["implementationRecoveryReopens"]))
        self.assertIn("IMPLEMENTATION_RECOVERY_REOPEN_MAX_ROUNDS_EXCEEDED", {violation.code for violation in third.pipeline_result.violations})

    def test_exhausted_changes_requested_fix_reopen_preserves_review_body(self):
        with self.project() as fixture:
            prompt_path = fixture.root / "reopen-prompt.txt"
            counter = fixture.root / "implementer-count.txt"
            implementer = fixture.root / "review-fix-implementer.py"
            implementer.write_text(
                "from pathlib import Path\n"
                "import sys\n"
                f"counter = Path(r'{counter}')\n"
                f"prompt_path = Path(r'{prompt_path}')\n"
                "prompt = sys.stdin.read()\n"
                "value = int(counter.read_text(encoding='utf-8')) if counter.exists() else 0\n"
                "counter.write_text(str(value + 1), encoding='utf-8')\n"
                "prompt_path.write_text(prompt, encoding='utf-8')\n"
                "assert 'Independent review Changes Requested context:' in prompt\n"
                "assert 'real player-facing development-request text input' in prompt\n"
                "Path('review-fix.txt').write_text('fixed after explicit reopen', encoding='utf-8')\n",
                encoding="utf-8",
            )
            fixture.config = self.write_config(
                fixture.root / "project-config.json",
                fixture.repo,
                implementer=f'"{sys.executable}" "{implementer}"',
                implementation_max_recovery_rounds=1,
                implementation_max_recovery_reopens=1,
            )
            record_path, record, candidate_sha = self.create_review_changes_requested_blocked_run(fixture, "Review fix reopen", 1)
            review_stdout = (
                "## Decision: Changes Requested\n\n"
                "Blocking finding: real player-facing development-request text input is missing.\n"
            )
            record["status"] = "IMPLEMENTATION_FAILED"
            record["nextStage"] = "human_intervention"
            record["implementationFailure"] = {
                "status": "IMPLEMENTATION_FAILED",
                "exitCode": "1",
                "timedOut": "false",
                "stdout": "",
                "stderr": "implementer failed while applying review fix",
                "headBefore": candidate_sha,
                "headAfter": candidate_sha,
                "changedFiles": [],
                "reasonCodes": ["IMPLEMENTER_COMMAND_FAILED"],
                "recoveryStage": "implementation_recovery",
            }
            record["implementationRecoveryAttempts"] = [
                {
                    "round": 1,
                    "reopenEpoch": 0,
                    "maxRounds": 1,
                    "status": "RECOVERY_IMPLEMENTER_PENDING",
                    "priorStatus": "IMPLEMENTATION_FAILED",
                    "priorExitCode": "1",
                    "priorTimedOut": "false",
                    "priorStdout": "",
                    "priorStderr": "implementer failed while applying review fix",
                    "headBefore": candidate_sha,
                    "headAfter": candidate_sha,
                    "changedFiles": [],
                    "reasonCodes": ["IMPLEMENTER_COMMAND_FAILED"],
                    "recoveryImplementerStatus": "IMPLEMENTATION_FAILED",
                    "recoveryImplementerViolationCodes": ["IMPLEMENTER_COMMAND_FAILED"],
                }
            ]
            record["implementationRecoveryBlock"] = {
                "status": "BLOCKED",
                "reasonCode": "IMPLEMENTATION_RECOVERY_MAX_ROUNDS_EXCEEDED",
                "message": "implementation recovery reached the configured maximum recovery rounds",
                "evidence": {"max_recovery_rounds": "1", "status": "IMPLEMENTATION_FAILED"},
            }
            record_path.write_text(json.dumps(record, indent=2, sort_keys=True), encoding="utf-8")
            record_path.with_name("review-runtime.json").write_text(
                json.dumps({"status": "PASS", "decision": "Changes Requested", "reviewed_sha": candidate_sha, "exit_code": 0, "stdout": review_stdout, "stderr": "", "violations": []}, indent=2, sort_keys=True),
                encoding="utf-8",
            )

            result = RunService(pipeline=RunPipeline(publisher=FakePublisher(fixture.repo))).run(
                RunRequest(fixture.repo, "Review fix reopen", 1, fixture.config, reopen_implementation_recovery=True)
            )
            archived = json.loads((fixture.repo / ".agent-workflow" / "runs" / "001-review-fix-reopen" / "ados-run.json").read_text(encoding="utf-8"))
            prompt = prompt_path.read_text(encoding="utf-8")

        self.assertEqual("COMPLETE", result.status)
        self.assertTrue(result.resumed)
        self.assertEqual(record["runId"], archived["runId"])
        self.assertIn("real player-facing development-request text input", prompt)
        self.assertEqual(1, len(archived["implementationRecoveryReopens"]))

    def test_partial_implementation_changes_survive_retry(self):
        with self.project() as fixture:
            counter = fixture.root / "implementer-count.txt"
            implementer = fixture.root / "partial-then-success.py"
            implementer.write_text(
                "from pathlib import Path\n"
                "import sys\n"
                f"counter = Path(r'{counter}')\n"
                "value = int(counter.read_text(encoding='utf-8')) if counter.exists() else 0\n"
                "counter.write_text(str(value + 1), encoding='utf-8')\n"
                "if value == 0:\n"
                "    Path('partial.txt').write_text('partial work', encoding='utf-8')\n"
                "    sys.exit(8)\n"
                "Path('final.txt').write_text(Path('partial.txt').read_text(encoding='utf-8') + ' completed', encoding='utf-8')\n",
                encoding="utf-8",
            )
            fixture.config = self.write_config(fixture.root / "project-config.json", fixture.repo, implementer=f'"{sys.executable}" "{implementer}"')

            result = RunService(pipeline=RunPipeline(publisher=FakePublisher(fixture.repo))).run(RunRequest(fixture.repo, "Partial implementer", None, fixture.config))
            record = json.loads((fixture.repo / ".agent-workflow" / "runs" / "001-partial-implementer" / "ados-run.json").read_text(encoding="utf-8"))

        self.assertEqual("COMPLETE", result.status)
        self.assertIn("partial.txt", record["implementationRecoveryAttempts"][0]["changedFiles"])
        self.assertIn("final.txt", result.pipeline_result.candidate.changed_files)

    def test_implementation_recovery_wrong_branch_blocks(self):
        with self.project() as fixture:
            implementer = fixture.root / "wrong-branch-implementer.py"
            implementer.write_text(
                "import subprocess, sys\n"
                "subprocess.run(['git', 'checkout', '-b', 'wrong-implementation-recovery'], check=True, capture_output=True, text=True)\n"
                "sys.exit(9)\n",
                encoding="utf-8",
            )
            fixture.config = self.write_config(fixture.root / "project-config.json", fixture.repo, implementer=f'"{sys.executable}" "{implementer}"')

            result = RunService(pipeline=RunPipeline(publisher=FakePublisher(fixture.repo))).run(RunRequest(fixture.repo, "Wrong branch implementation recovery", 1, fixture.config))
            record_path = Path(result.run_record.feature_worktree) / ".agent-workflow" / "runs" / "001-wrong-branch-implementation-recovery" / "ados-run.json"
            record = json.loads(record_path.read_text(encoding="utf-8"))

        self.assertEqual("IMPLEMENTATION_FAILED", result.status)
        self.assertIn("IMPLEMENTATION_RECOVERY_BRANCH_MISMATCH", {violation.code for violation in result.pipeline_result.violations})
        self.assertEqual("IMPLEMENTATION_RECOVERY_BRANCH_MISMATCH", record["implementationRecoveryBlock"]["reasonCode"])

    def test_implementation_failure_no_change_then_adjudication_composes(self):
        with self.project() as fixture:
            implementer_counter = fixture.root / "implementer-count.txt"
            implementer = fixture.root / "failure-then-no-change.py"
            verifier = fixture.root / "verifier.py"
            implementer.write_text(
                "from pathlib import Path\n"
                "import sys\n"
                f"counter = Path(r'{implementer_counter}')\n"
                "value = int(counter.read_text(encoding='utf-8')) if counter.exists() else 0\n"
                "counter.write_text(str(value + 1), encoding='utf-8')\n"
                "if value == 0:\n"
                "    sys.exit(9)\n",
                encoding="utf-8",
            )
            verifier.write_text("print('NO_CHANGES_VERIFIED')\n", encoding="utf-8")
            fixture.config = self.write_config(fixture.root / "project-config.json", fixture.repo, implementer=f'"{sys.executable}" "{implementer}"', reviewer=f'"{sys.executable}" "{verifier}"')

            result = RunService(pipeline=RunPipeline(publisher=FakePublisher(fixture.repo))).run(RunRequest(fixture.repo, "Failure then no changes", None, fixture.config))
            stages = [stage.id for stage in result.pipeline_result.stages]

        self.assertEqual("NO_CHANGES", result.status)
        self.assertIn("implementation_recovery_implementer", stages)
        self.assertIn("no_change_verification", stages)

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
            verifier = fixture.root / "no-change-verifier.py"
            verifier.write_text("print('NO_CHANGES_VERIFIED')\n", encoding="utf-8")
            fixture.config = self.write_config(fixture.root / "project-config.json", fixture.repo, implementer_mode="no_change", reviewer=f'"{sys.executable}" "{verifier}"')
            publisher = FakePublisher(fixture.repo)
            before_head = self.head(fixture.repo)
            result = RunService(pipeline=RunPipeline(publisher=publisher)).run(RunRequest(fixture.repo, "Already satisfied", None, fixture.config))
            status = StatusService().run(StatusRequest(fixture.repo, fixture.config))
            after_head = self.head(fixture.repo)

        self.assertEqual("NO_CHANGES", result.status)
        self.assertEqual(before_head, after_head)
        self.assertIsNone(result.pipeline_result.validation)
        self.assertIsNone(result.pipeline_result.review)
        self.assertEqual("NO_CHANGES_VERIFIED", result.pipeline_result.no_change_verification.decision)
        self.assertEqual([], publisher.calls)
        self.assertFalse(Path(result.plan.feature_worktree).exists())
        self.assertEqual("IDLE", status.status)
        self.assertEqual("None", status.spec.evidence["active_spec"])

    def test_no_change_run_does_not_increment_latest_merged_spec(self):
        with self.project(specs=[91], implementer_mode="no_change") as fixture:
            verifier = fixture.root / "no-change-verifier.py"
            verifier.write_text("print('NO_CHANGES_VERIFIED')\n", encoding="utf-8")
            fixture.config = self.write_config(fixture.root / "project-config.json", fixture.repo, implementer_mode="no_change", reviewer=f'"{sys.executable}" "{verifier}"')
            self.write_archive(fixture.repo, spec="091-previous", merge_commit=self.head(fixture.repo))
            result = RunService(pipeline=RunPipeline(publisher=FakePublisher(fixture.repo))).run(RunRequest(fixture.repo, "No merge evidence", 92, fixture.config))
            status = StatusService().run(StatusRequest(fixture.repo, fixture.config))

        self.assertEqual("NO_CHANGES", result.status)
        self.assertEqual("091", status.spec.evidence["latest_merged_spec"])
        self.assertEqual("None", status.spec.evidence["active_spec"])

    def test_false_no_changes_invokes_recovery_implementer_and_continues_pipeline(self):
        with self.project() as fixture:
            implementer_counter = fixture.root / "implementer-count.txt"
            verifier = fixture.root / "verifier.py"
            implementer = fixture.root / "implementer.py"
            implementer.write_text(
                "from pathlib import Path\n"
                f"counter = Path(r'{implementer_counter}')\n"
                "value = int(counter.read_text(encoding='utf-8')) if counter.exists() else 0\n"
                "counter.write_text(str(value + 1), encoding='utf-8')\n"
                "handoff = __import__('sys').stdin.read()\n"
                "if 'NO_CHANGES recovery context:' in handoff:\n"
                "    Path('feature.txt').write_text('implemented after verifier rejection', encoding='utf-8')\n",
                encoding="utf-8",
            )
            verifier.write_text(
                "from pathlib import Path\n"
                f"counter = Path(r'{fixture.root / 'verifier-count.txt'}')\n"
                "value = int(counter.read_text(encoding='utf-8')) if counter.exists() else 0\n"
                "counter.write_text(str(value + 1), encoding='utf-8')\n"
                "print('FEATURE_MISSING' if value == 0 else 'Approved')\n",
                encoding="utf-8",
            )
            fixture.config = self.write_config(fixture.root / "project-config.json", fixture.repo, implementer=f'"{sys.executable}" "{implementer}"', reviewer=f'"{sys.executable}" "{verifier}"')

            result = RunService(pipeline=RunPipeline(publisher=FakePublisher(fixture.repo))).run(RunRequest(fixture.repo, "False no changes", None, fixture.config))
            stages = [stage.id for stage in result.pipeline_result.stages]
            record = json.loads((fixture.repo / ".agent-workflow" / "runs" / "001-false-no-changes" / "ados-run.json").read_text(encoding="utf-8"))
            implementer_count = implementer_counter.read_text(encoding="utf-8")

        self.assertEqual("COMPLETE", result.status)
        self.assertIn("no_change_verification", stages)
        self.assertIn("no_change_recovery_implementer", stages)
        self.assertEqual("2", implementer_count)
        self.assertEqual("FEATURE_MISSING", record["noChangeAdjudicationAttempts"][0]["verifierDecision"])
        self.assertEqual(result.pipeline_result.candidate.candidate_sha, result.pipeline_result.validation.head_after)
        self.assertEqual(result.pipeline_result.candidate.candidate_sha, result.pipeline_result.review.reviewed_sha)

    def test_no_change_verifier_ambiguous_blocks_without_publication(self):
        with self.project(implementer_mode="no_change") as fixture:
            verifier = fixture.root / "ambiguous-verifier.py"
            verifier.write_text("print('This might already exist')\n", encoding="utf-8")
            fixture.config = self.write_config(fixture.root / "project-config.json", fixture.repo, implementer_mode="no_change", reviewer=f'"{sys.executable}" "{verifier}"')
            publisher = FakePublisher(fixture.repo)

            result = RunService(pipeline=RunPipeline(publisher=publisher)).run(RunRequest(fixture.repo, "Ambiguous no changes", None, fixture.config))
            record_path = Path(result.run_record.feature_worktree) / ".agent-workflow" / "runs" / "001-ambiguous-no-changes" / "ados-run.json"
            record = json.loads(record_path.read_text(encoding="utf-8"))

        self.assertEqual("NO_CHANGES_AMBIGUOUS", result.status)
        self.assertEqual([], publisher.calls)
        self.assertEqual("NO_CHANGES_AMBIGUOUS", record["noChangeAdjudicationBlock"]["reasonCode"])

    def test_repeated_false_no_changes_reaches_configured_bound(self):
        with self.project(implementer_mode="no_change") as fixture:
            verifier = fixture.root / "missing-verifier.py"
            verifier.write_text("print('FEATURE_MISSING')\n", encoding="utf-8")
            fixture.config = self.write_config(
                fixture.root / "project-config.json",
                fixture.repo,
                implementer_mode="no_change",
                reviewer=f'"{sys.executable}" "{verifier}"',
                validation_max_recovery_rounds=1,
            )
            config = json.loads(fixture.config.read_text(encoding="utf-8"))
            config["execution_policy"]["validation"]["max_no_change_recovery_rounds"] = 1
            fixture.config.write_text(json.dumps(config), encoding="utf-8")

            result = RunService(pipeline=RunPipeline(publisher=FakePublisher(fixture.repo))).run(RunRequest(fixture.repo, "Repeated false no changes", None, fixture.config))
            record_path = Path(result.run_record.feature_worktree) / ".agent-workflow" / "runs" / "001-repeated-false-no-changes" / "ados-run.json"
            record = json.loads(record_path.read_text(encoding="utf-8"))

        self.assertEqual("NO_CHANGES_AMBIGUOUS", result.status)
        self.assertEqual(2, len(record["noChangeAdjudicationAttempts"]))
        self.assertEqual("NO_CHANGE_RECOVERY_MAX_ROUNDS_EXCEEDED", record["noChangeAdjudicationBlock"]["reasonCode"])

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
            verifier = fixture.root / "no-change-verifier.py"
            verifier.write_text("print('NO_CHANGES_VERIFIED')\n", encoding="utf-8")
            fixture.config = self.write_config(fixture.root / "project-config.json", fixture.repo, implementer_mode="no_change", reviewer=f'"{sys.executable}" "{verifier}"')
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
            record_path = Path(result.run_record.feature_worktree) / ".agent-workflow" / "runs" / "001-validation-fails" / "ados-run.json"
            run_record = json.loads(record_path.read_text(encoding="utf-8"))
            status = StatusService().run(StatusRequest(fixture.repo, fixture.config))

        self.assertEqual("VALIDATION_FAILED", result.status)
        self.assertIsNone(result.pipeline_result.review)
        self.assertIn("VALIDATION_RECOVERY_NO_CHANGES", {violation.code for violation in result.pipeline_result.violations})
        self.assertEqual("human_intervention", run_record["nextStage"])
        self.assertEqual("VALIDATION_RECOVERY_NO_CHANGES", run_record["validationRecoveryBlock"]["reasonCode"])
        self.assertEqual(1, len(run_record["validationRecoveryAttempts"]))
        self.assertEqual("True", status.workflow.evidence["resumable"])
        self.assertEqual("VALIDATION_RECOVERY_NO_CHANGES", status.workflow.evidence["validation_recovery_block_reason"])

    def test_validation_timeout_persists_evidence_and_does_not_review(self):
        with self.project() as fixture:
            reviewer_marker = fixture.root / "review-ran.txt"
            reviewer = fixture.root / "reviewer.py"
            reviewer.write_text(
                f"from pathlib import Path\nPath(r'{reviewer_marker}').write_text('reviewed', encoding='utf-8')\nprint('Approved')\n",
                encoding="utf-8",
            )
            config = json.loads(fixture.config.read_text(encoding="utf-8"))
            config["roles"]["reviewer"] = f'"{sys.executable}" "{reviewer}"'
            config["execution_policy"]["review"]["reviewer"] = f'"{sys.executable}" "{reviewer}"'
            config["execution_policy"]["validation"] = {
                "commands": [f'"{sys.executable}" -c "import time; print(\'validation started\', flush=True); time.sleep(30)"'],
                "timeout_ms": 200,
            }
            fixture.config.write_text(json.dumps(config), encoding="utf-8")

            result = RunService(pipeline=RunPipeline(publisher=FakePublisher(fixture.repo))).run(RunRequest(fixture.repo, "Validation timeout", None, fixture.config))
            record_path = Path(result.run_record.feature_worktree) / ".agent-workflow" / "runs" / "001-validation-timeout" / "ados-run.json"
            validation_artifact = json.loads(record_path.with_name("validation-runtime.json").read_text(encoding="utf-8"))
            run_record = json.loads(record_path.read_text(encoding="utf-8"))

        self.assertEqual("VALIDATION_FAILED", result.status)
        self.assertIsNone(result.pipeline_result.review)
        self.assertFalse(reviewer_marker.exists())
        self.assertEqual("BLOCK", validation_artifact["status"])
        self.assertTrue(validation_artifact["commands"][0]["timed_out"])
        self.assertIn("validation started", validation_artifact["commands"][0]["stdout"])
        self.assertEqual("VALIDATION_COMMAND_TIMED_OUT", validation_artifact["violations"][0]["code"])
        self.assertEqual("VALIDATION_COMMAND_TIMED_OUT", run_record["validationFailure"]["failedCommands"][0]["reasonCode"])
        self.assertEqual("validation-runtime.json", Path(run_record["validationFailure"]["artifact"]).name)

    def test_validation_recovery_reaches_configured_maximum_and_blocks_review(self):
        with self.project() as fixture:
            implementer_counter = fixture.root / "implementer-count.txt"
            reviewer_marker = fixture.root / "review-ran.txt"
            implementer = fixture.root / "implementer-new-candidate.py"
            validator = fixture.root / "validator-always-fails.py"
            reviewer = fixture.root / "reviewer.py"
            implementer.write_text(
                "from pathlib import Path\n"
                f"counter = Path(r'{implementer_counter}')\n"
                "value = int(counter.read_text(encoding='utf-8')) if counter.exists() else 0\n"
                "counter.write_text(str(value + 1), encoding='utf-8')\n"
                "Path(f'candidate-{value + 1}.txt').write_text(str(value + 1), encoding='utf-8')\n",
                encoding="utf-8",
            )
            validator.write_text("import sys\nsys.exit(7)\n", encoding="utf-8")
            reviewer.write_text(
                f"from pathlib import Path\nPath(r'{reviewer_marker}').write_text('reviewed', encoding='utf-8')\nprint('Approved')\n",
                encoding="utf-8",
            )
            fixture.config = self.write_config(
                fixture.root / "project-config.json",
                fixture.repo,
                implementer=f'"{sys.executable}" "{implementer}"',
                reviewer=f'"{sys.executable}" "{reviewer}"',
                validation_commands=[f'"{sys.executable}" "{validator}"'],
                validation_max_recovery_rounds=2,
            )

            result = RunService(pipeline=RunPipeline(publisher=FakePublisher(fixture.repo))).run(RunRequest(fixture.repo, "Bounded validation recovery", None, fixture.config))
            record_path = Path(result.run_record.feature_worktree) / ".agent-workflow" / "runs" / "001-bounded-validation-recovery" / "ados-run.json"
            run_record = json.loads(record_path.read_text(encoding="utf-8"))
            implementer_count = implementer_counter.read_text(encoding="utf-8")
            reviewer_ran = reviewer_marker.exists()

        self.assertEqual("VALIDATION_FAILED", result.status)
        self.assertIsNone(result.pipeline_result.review)
        self.assertFalse(reviewer_ran)
        self.assertEqual("3", implementer_count)
        self.assertEqual(2, len(run_record["validationRecoveryAttempts"]))
        self.assertEqual("VALIDATION_RECOVERY_MAX_ROUNDS_EXCEEDED", run_record["validationRecoveryBlock"]["reasonCode"])
        self.assertIn("VALIDATION_RECOVERY_MAX_ROUNDS_EXCEEDED", {violation.code for violation in result.pipeline_result.violations})

    def test_exhausted_validation_recovery_without_runtime_change_remains_blocked(self):
        with self.project() as fixture:
            implementer_counter = fixture.root / "implementer-count.txt"
            validator = fixture.root / "validator.py"
            implementer = fixture.root / "implementer-new-candidate.py"
            implementer.write_text(
                "from pathlib import Path\n"
                f"counter = Path(r'{implementer_counter}')\n"
                "value = int(counter.read_text(encoding='utf-8')) if counter.exists() else 0\n"
                "counter.write_text(str(value + 1), encoding='utf-8')\n"
                "Path(f'candidate-{value + 1}.txt').write_text(str(value + 1), encoding='utf-8')\n",
                encoding="utf-8",
            )
            validator.write_text("import sys\nsys.exit(7)\n", encoding="utf-8")
            fixture.config = self.write_config(
                fixture.root / "project-config.json",
                fixture.repo,
                implementer=f'"{sys.executable}" "{implementer}"',
                validation_commands=[f'"{sys.executable}" "{validator}"'],
                validation_max_recovery_rounds=1,
            )

            first = RunService(pipeline=RunPipeline(publisher=FakePublisher(fixture.repo))).run(RunRequest(fixture.repo, "Exhaust unchanged recovery", None, fixture.config))
            second = RunService(pipeline=RunPipeline(publisher=FakePublisher(fixture.repo))).run(RunRequest(fixture.repo, "Exhaust unchanged recovery", None, fixture.config))
            record_path = Path(first.run_record.feature_worktree) / ".agent-workflow" / "runs" / "001-exhaust-unchanged-recovery" / "ados-run.json"
            run_record = json.loads(record_path.read_text(encoding="utf-8"))
            stages = [stage.id for stage in second.pipeline_result.stages]
            implementer_count = implementer_counter.read_text(encoding="utf-8")

        self.assertTrue(second.resumed)
        self.assertEqual("VALIDATION_FAILED", first.status)
        self.assertEqual("VALIDATION_FAILED", second.status)
        self.assertEqual("2", implementer_count)
        self.assertIn("recoveryEngine", run_record["validationRecoveryBlock"])
        self.assertNotIn("validation_recovery_reopen", stages)
        self.assertIn("VALIDATION_RECOVERY_MAX_ROUNDS_EXCEEDED", {violation.code for violation in second.pipeline_result.violations})

    def test_legacy_exhausted_validation_recovery_reopens_once_after_runtime_upgrade(self):
        with self.project() as fixture:
            implementer_counter = fixture.root / "implementer-count.txt"
            reviewer_counter = fixture.root / "review-count.txt"
            validator = fixture.root / "validator.py"
            reviewer = fixture.root / "reviewer.py"
            implementer = fixture.root / "implementer-new-candidate.py"
            implementer.write_text(
                "from pathlib import Path\n"
                f"counter = Path(r'{implementer_counter}')\n"
                "value = int(counter.read_text(encoding='utf-8')) if counter.exists() else 0\n"
                "counter.write_text(str(value + 1), encoding='utf-8')\n"
                "Path(f'candidate-{value + 1}.txt').write_text(str(value + 1), encoding='utf-8')\n",
                encoding="utf-8",
            )
            validator.write_text(
                "from pathlib import Path\n"
                "import sys\n"
                "if not Path('candidate-3.txt').exists():\n"
                "    sys.exit(7)\n",
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
                validation_commands=[f'"{sys.executable}" "{validator}"'],
                validation_max_recovery_rounds=1,
            )
            first = RunService(pipeline=RunPipeline(publisher=FakePublisher(fixture.repo))).run(RunRequest(fixture.repo, "Legacy exhausted recovery", None, fixture.config))
            record_path = Path(first.run_record.feature_worktree) / ".agent-workflow" / "runs" / "001-legacy-exhausted-recovery" / "ados-run.json"
            legacy_record = json.loads(record_path.read_text(encoding="utf-8"))
            legacy_record["validationRecoveryBlock"].pop("recoveryEngine", None)
            legacy_record["validationRecoveryBlock"].pop("policyFingerprint", None)
            record_path.write_text(json.dumps(legacy_record, indent=2), encoding="utf-8")

            second = RunService(pipeline=RunPipeline(publisher=FakePublisher(fixture.repo))).run(RunRequest(fixture.repo, "Legacy exhausted recovery", None, fixture.config))
            completed_record = second.pipeline_result.run_record
            stages = [stage.id for stage in second.pipeline_result.stages]
            implementer_count = implementer_counter.read_text(encoding="utf-8")
            reviewer_count = reviewer_counter.read_text(encoding="utf-8")

        self.assertTrue(second.resumed)
        self.assertEqual("VALIDATION_FAILED", first.status)
        self.assertEqual("COMPLETE", second.status)
        self.assertEqual(first.run_record.run_id, second.run_record.run_id)
        self.assertIn("validation_recovery_reopen", stages)
        self.assertEqual("3", implementer_count)
        self.assertEqual("1", reviewer_count)
        self.assertEqual(1, len(completed_record["validationRecoveryReopens"]))
        self.assertEqual("legacy_exhausted_block_without_runtime_identity", completed_record["validationRecoveryReopens"][0]["reason"])
        self.assertEqual(1, completed_record["validationRecoveryReopens"][0]["previousRecoveryAttemptCount"])
        self.assertEqual(second.pipeline_result.candidate.candidate_sha, second.pipeline_result.validation.head_after)
        self.assertEqual(second.pipeline_result.candidate.candidate_sha, second.pipeline_result.review.reviewed_sha)

    def test_legacy_exhausted_validation_recovery_second_reopen_blocks(self):
        with self.project() as fixture:
            implementer_counter = fixture.root / "implementer-count.txt"
            validator = fixture.root / "validator.py"
            implementer = fixture.root / "implementer-new-candidate.py"
            implementer.write_text(
                "from pathlib import Path\n"
                f"counter = Path(r'{implementer_counter}')\n"
                "value = int(counter.read_text(encoding='utf-8')) if counter.exists() else 0\n"
                "counter.write_text(str(value + 1), encoding='utf-8')\n"
                "Path(f'candidate-{value + 1}.txt').write_text(str(value + 1), encoding='utf-8')\n",
                encoding="utf-8",
            )
            validator.write_text("import sys\nsys.exit(7)\n", encoding="utf-8")
            fixture.config = self.write_config(
                fixture.root / "project-config.json",
                fixture.repo,
                implementer=f'"{sys.executable}" "{implementer}"',
                validation_commands=[f'"{sys.executable}" "{validator}"'],
                validation_max_recovery_rounds=1,
            )
            first = RunService(pipeline=RunPipeline(publisher=FakePublisher(fixture.repo))).run(RunRequest(fixture.repo, "Legacy reopen once", None, fixture.config))
            record_path = Path(first.run_record.feature_worktree) / ".agent-workflow" / "runs" / "001-legacy-reopen-once" / "ados-run.json"
            legacy_record = json.loads(record_path.read_text(encoding="utf-8"))
            legacy_record["validationRecoveryBlock"].pop("recoveryEngine", None)
            legacy_record["validationRecoveryBlock"].pop("policyFingerprint", None)
            record_path.write_text(json.dumps(legacy_record, indent=2), encoding="utf-8")

            second = RunService(pipeline=RunPipeline(publisher=FakePublisher(fixture.repo))).run(RunRequest(fixture.repo, "Legacy reopen once", None, fixture.config))
            third = RunService(pipeline=RunPipeline(publisher=FakePublisher(fixture.repo))).run(RunRequest(fixture.repo, "Legacy reopen once", None, fixture.config))
            implementer_count = implementer_counter.read_text(encoding="utf-8")

        self.assertEqual("VALIDATION_FAILED", second.status)
        self.assertEqual("VALIDATION_FAILED", third.status)
        self.assertEqual("4", implementer_count)
        self.assertIn("VALIDATION_RECOVERY_MAX_ROUNDS_EXCEEDED", {violation.code for violation in third.pipeline_result.violations})

    def test_legacy_exhausted_validation_recovery_reopen_blocks_wrong_branch(self):
        with self.project() as fixture:
            validator = fixture.root / "validator.py"
            validator.write_text("import sys\nsys.exit(7)\n", encoding="utf-8")
            fixture.config = self.write_config(
                fixture.root / "project-config.json",
                fixture.repo,
                implementer_mode="count",
                validation_commands=[f'"{sys.executable}" "{validator}"'],
                validation_max_recovery_rounds=1,
            )
            first = RunService(pipeline=RunPipeline(publisher=FakePublisher(fixture.repo))).run(RunRequest(fixture.repo, "Legacy reopen wrong branch", None, fixture.config))
            worktree = Path(first.run_record.feature_worktree)
            record_path = worktree / ".agent-workflow" / "runs" / "001-legacy-reopen-wrong-branch" / "ados-run.json"
            legacy_record = json.loads(record_path.read_text(encoding="utf-8"))
            legacy_record["validationRecoveryBlock"].pop("recoveryEngine", None)
            legacy_record["validationRecoveryBlock"].pop("policyFingerprint", None)
            record_path.write_text(json.dumps(legacy_record, indent=2), encoding="utf-8")
            self.git(worktree, "checkout", "-b", "wrong-validation-reopen-branch")

            second = RunService(pipeline=RunPipeline(publisher=FakePublisher(fixture.repo))).run(RunRequest(fixture.repo, "Legacy reopen wrong branch", None, fixture.config))

        self.assertEqual("BLOCKED", second.status)
        self.assertIn("ACTIVE_WORKTREE_PRESENT", self.codes(second))
        self.assertIn("FEATURE_BRANCH_EXISTS", self.codes(second))
        self.assertIn("WORKTREE_PATH_EXISTS", self.codes(second))

    def test_validation_failure_invokes_automatic_recovery_before_review(self):
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
            completed_record = json.loads((fixture.repo / ".agent-workflow" / "runs" / "001-validation-recovery" / "ados-run.json").read_text(encoding="utf-8"))
            candidate_after = first.pipeline_result.candidate.candidate_sha
            recovery_attempt = completed_record["validationRecoveryAttempts"][0]
            implementer_count = implementer_counter.read_text(encoding="utf-8")
            bootstrap_count = bootstrap_counter.read_text(encoding="utf-8")
            reviewer_count = reviewer_counter.read_text(encoding="utf-8")

        self.assertEqual("COMPLETE", first.status)
        self.assertNotIn("validationFailure", completed_record)
        self.assertEqual("RECOVERY_CANDIDATE_RECORDED", recovery_attempt["status"])
        self.assertEqual("BLOCK", recovery_attempt["failedValidationStatus"])
        self.assertEqual(["VALIDATION_COMMAND_FAILED"], recovery_attempt["failedCommandCodes"])
        self.assertEqual("VALIDATION_COMMAND_FAILED", recovery_attempt["failedCommands"][0]["reasonCode"])
        self.assertIn("validator stdout", recovery_attempt["failedCommands"][0]["stdout"])
        self.assertIn("validator stderr", recovery_attempt["failedCommands"][0]["stderr"])
        self.assertNotEqual(recovery_attempt["failedCandidateSha"], candidate_after)
        self.assertEqual(candidate_after, recovery_attempt["recoveryCandidateSha"])
        self.assertIn("validation_recovery_implementer", [stage.id for stage in first.pipeline_result.stages])
        self.assertEqual(candidate_after, first.pipeline_result.validation.head_after)
        self.assertEqual(candidate_after, first.pipeline_result.review.reviewed_sha)
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
        self.assertEqual("2", implementer_count)
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
        self.assertEqual("3", implementer_count)

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

    def test_validation_recovery_then_review_changes_requested_uses_final_exact_head(self):
        with self.project() as fixture:
            implementer_counter = fixture.root / "implementer-count.txt"
            reviewer_counter = fixture.root / "review-count.txt"
            implementer = fixture.root / "implementer-validation-then-review.py"
            validator = fixture.root / "validator.py"
            reviewer = fixture.root / "reviewer.py"
            implementer.write_text(
                "from pathlib import Path\n"
                f"counter = Path(r'{implementer_counter}')\n"
                "value = int(counter.read_text(encoding='utf-8')) if counter.exists() else 0\n"
                "counter.write_text(str(value + 1), encoding='utf-8')\n"
                "if value == 0:\n"
                "    Path('implementation.txt').write_text('implemented', encoding='utf-8')\n"
                "elif value == 1:\n"
                "    Path('validation-fix.txt').write_text('fixed validation', encoding='utf-8')\n"
                "else:\n"
                "    Path('review-fix.txt').write_text('fixed review', encoding='utf-8')\n",
                encoding="utf-8",
            )
            validator.write_text(
                "from pathlib import Path\n"
                "import sys\n"
                "if not Path('validation-fix.txt').exists():\n"
                "    sys.exit(8)\n",
                encoding="utf-8",
            )
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
                implementer=f'"{sys.executable}" "{implementer}"',
                reviewer=f'"{sys.executable}" "{reviewer}"',
                validation_commands=[f'"{sys.executable}" "{validator}"'],
            )

            result = RunService(pipeline=RunPipeline(publisher=FakePublisher(fixture.repo))).run(RunRequest(fixture.repo, "Validation and review recovery", None, fixture.config))
            stages = [stage.id for stage in result.pipeline_result.stages]
            final_candidate = result.pipeline_result.candidate.candidate_sha
            completed_record = json.loads((fixture.repo / ".agent-workflow" / "runs" / "001-validation-and-review-recovery" / "ados-run.json").read_text(encoding="utf-8"))
            implementer_count = implementer_counter.read_text(encoding="utf-8")
            reviewer_count = reviewer_counter.read_text(encoding="utf-8")

        self.assertEqual("COMPLETE", result.status)
        self.assertIn("validation_recovery_implementer", stages)
        self.assertIn("implementer_fix", stages)
        self.assertEqual("3", implementer_count)
        self.assertEqual("2", reviewer_count)
        self.assertEqual(final_candidate, result.pipeline_result.validation.head_after)
        self.assertEqual(final_candidate, result.pipeline_result.review.reviewed_sha)
        self.assertEqual(final_candidate, result.pipeline_result.exact_head_gate["current_head_sha"])
        self.assertNotEqual(completed_record["validationRecoveryAttempts"][0]["failedCandidateSha"], final_candidate)

    def test_false_no_changes_validation_recovery_and_review_fix_compose_to_final_head(self):
        with self.project() as fixture:
            implementer_counter = fixture.root / "implementer-count.txt"
            reviewer_counter = fixture.root / "reviewer-count.txt"
            implementer = fixture.root / "implementer-composed-recovery.py"
            validator = fixture.root / "validator.py"
            reviewer = fixture.root / "reviewer-and-verifier.py"
            implementer.write_text(
                "from pathlib import Path\n"
                "import sys\n"
                f"counter = Path(r'{implementer_counter}')\n"
                "value = int(counter.read_text(encoding='utf-8')) if counter.exists() else 0\n"
                "counter.write_text(str(value + 1), encoding='utf-8')\n"
                "handoff = sys.stdin.read()\n"
                "if value == 1:\n"
                "    Path('implementation-after-no-change.txt').write_text('candidate B', encoding='utf-8')\n"
                "elif value == 2:\n"
                "    Path('validation-fix.txt').write_text('candidate C', encoding='utf-8')\n"
                "elif value == 3:\n"
                "    Path('review-fix.txt').write_text('candidate D', encoding='utf-8')\n",
                encoding="utf-8",
            )
            validator.write_text(
                "from pathlib import Path\n"
                "import sys\n"
                "if not Path('validation-fix.txt').exists():\n"
                "    sys.exit(9)\n",
                encoding="utf-8",
            )
            reviewer.write_text(
                "from pathlib import Path\n"
                f"counter = Path(r'{reviewer_counter}')\n"
                "value = int(counter.read_text(encoding='utf-8')) if counter.exists() else 0\n"
                "counter.write_text(str(value + 1), encoding='utf-8')\n"
                "if value == 0:\n"
                "    print('FEATURE_MISSING')\n"
                "elif value == 1:\n"
                "    print('Changes Requested')\n"
                "else:\n"
                "    print('Approved')\n",
                encoding="utf-8",
            )
            fixture.config = self.write_config(
                fixture.root / "project-config.json",
                fixture.repo,
                implementer=f'"{sys.executable}" "{implementer}"',
                reviewer=f'"{sys.executable}" "{reviewer}"',
                validation_commands=[f'"{sys.executable}" "{validator}"'],
            )

            result = RunService(pipeline=RunPipeline(publisher=FakePublisher(fixture.repo))).run(RunRequest(fixture.repo, "Composed no change recovery", None, fixture.config))
            stages = [stage.id for stage in result.pipeline_result.stages]
            final_candidate = result.pipeline_result.candidate.candidate_sha
            completed_record = json.loads((fixture.repo / ".agent-workflow" / "runs" / "001-composed-no-change-recovery" / "ados-run.json").read_text(encoding="utf-8"))
            implementer_count = implementer_counter.read_text(encoding="utf-8")
            reviewer_count = reviewer_counter.read_text(encoding="utf-8")

        self.assertEqual("COMPLETE", result.status)
        self.assertIn("no_change_verification", stages)
        self.assertIn("no_change_recovery_implementer", stages)
        self.assertIn("validation_recovery_implementer", stages)
        self.assertIn("implementer_fix", stages)
        self.assertEqual("4", implementer_count)
        self.assertEqual("3", reviewer_count)
        self.assertEqual("FEATURE_MISSING", completed_record["noChangeAdjudicationAttempts"][0]["verifierDecision"])
        self.assertEqual(final_candidate, result.pipeline_result.validation.head_after)
        self.assertEqual(final_candidate, result.pipeline_result.review.reviewed_sha)
        self.assertEqual(final_candidate, result.pipeline_result.exact_head_gate["current_head_sha"])

    def test_implementation_failure_no_change_validation_and_review_recovery_compose_to_final_head(self):
        with self.project() as fixture:
            implementer_counter = fixture.root / "implementer-count.txt"
            reviewer_counter = fixture.root / "reviewer-count.txt"
            implementer = fixture.root / "implementer-full-recovery-composition.py"
            validator = fixture.root / "validator.py"
            reviewer = fixture.root / "reviewer-full-composition.py"
            implementer.write_text(
                "from pathlib import Path\n"
                "import sys\n"
                f"counter = Path(r'{implementer_counter}')\n"
                "value = int(counter.read_text(encoding='utf-8')) if counter.exists() else 0\n"
                "counter.write_text(str(value + 1), encoding='utf-8')\n"
                "handoff = sys.stdin.read()\n"
                "if value == 0:\n"
                "    print('initial implementer failure', file=sys.stderr)\n"
                "    sys.exit(9)\n"
                "if value == 1:\n"
                "    assert 'Implementation failure recovery context:' in handoff\n"
                "elif value == 2:\n"
                "    Path('implementation-after-no-change.txt').write_text('candidate C', encoding='utf-8')\n"
                "elif value == 3:\n"
                "    Path('validation-fix.txt').write_text('candidate D', encoding='utf-8')\n"
                "elif value == 4:\n"
                "    Path('review-fix.txt').write_text('candidate E', encoding='utf-8')\n",
                encoding="utf-8",
            )
            validator.write_text(
                "from pathlib import Path\n"
                "import sys\n"
                "if not Path('validation-fix.txt').exists():\n"
                "    sys.exit(9)\n",
                encoding="utf-8",
            )
            reviewer.write_text(
                "from pathlib import Path\n"
                f"counter = Path(r'{reviewer_counter}')\n"
                "value = int(counter.read_text(encoding='utf-8')) if counter.exists() else 0\n"
                "counter.write_text(str(value + 1), encoding='utf-8')\n"
                "if value == 0:\n"
                "    print('FEATURE_MISSING')\n"
                "elif value == 1:\n"
                "    print('Changes Requested')\n"
                "else:\n"
                "    print('Approved')\n",
                encoding="utf-8",
            )
            fixture.config = self.write_config(
                fixture.root / "project-config.json",
                fixture.repo,
                implementer=f'"{sys.executable}" "{implementer}"',
                reviewer=f'"{sys.executable}" "{reviewer}"',
                validation_commands=[f'"{sys.executable}" "{validator}"'],
            )

            result = RunService(pipeline=RunPipeline(publisher=FakePublisher(fixture.repo))).run(RunRequest(fixture.repo, "Full autonomous recovery composition", None, fixture.config))
            stages = [stage.id for stage in result.pipeline_result.stages]
            final_candidate = result.pipeline_result.candidate.candidate_sha
            completed_record = json.loads((fixture.repo / ".agent-workflow" / "runs" / "001-full-autonomous-recovery-composition" / "ados-run.json").read_text(encoding="utf-8"))
            implementer_count = implementer_counter.read_text(encoding="utf-8")
            reviewer_count = reviewer_counter.read_text(encoding="utf-8")

        self.assertEqual("COMPLETE", result.status)
        self.assertIn("implementation_recovery_implementer", stages)
        self.assertIn("no_change_verification", stages)
        self.assertIn("no_change_recovery_implementer", stages)
        self.assertIn("validation_recovery_implementer", stages)
        self.assertIn("implementer_fix", stages)
        self.assertEqual("5", implementer_count)
        self.assertEqual("3", reviewer_count)
        self.assertEqual(1, len(completed_record["implementationRecoveryAttempts"]))
        self.assertEqual("FEATURE_MISSING", completed_record["noChangeAdjudicationAttempts"][0]["verifierDecision"])
        self.assertEqual(final_candidate, result.pipeline_result.validation.head_after)
        self.assertEqual(final_candidate, result.pipeline_result.review.reviewed_sha)
        self.assertEqual(final_candidate, result.pipeline_result.exact_head_gate["current_head_sha"])

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

    def test_review_blocked_runtime_decision_unavailable_resumes_at_review(self):
        with self.project(implementer_mode="count") as fixture:
            implementer_counter = fixture.root / "implementer-count.txt"
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
                "print('Waiting for the background full test suite to finish before issuing the final decision.' if value == 0 else 'Approved')\n",
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

            second = RunService(pipeline=RunPipeline(publisher=publisher)).run(RunRequest(fixture.repo, "Interrupted review output", None, fixture.config))
            validation_count = validation_counter.read_text(encoding="utf-8")
            reviewer_count = reviewer_counter.read_text(encoding="utf-8")
            implementer_count = implementer_counter.read_text(encoding="utf-8")

        self.assertEqual("REVIEW_BLOCKED", first.status)
        self.assertEqual("REVIEW_DECISION_UNAVAILABLE", blocked_record["reviewBlock"]["reasonCode"])
        self.assertEqual("review_runtime", blocked_record["reviewBlock"]["blockCause"])
        self.assertFalse(blocked_record["reviewBlock"]["transient"])
        self.assertEqual("True", status.workflow.evidence["resumable"])
        self.assertEqual("REVIEW_DECISION_UNAVAILABLE", status.workflow.evidence["review_block_reason"])
        self.assertEqual("review", status.workflow.evidence["resume_stage"])
        self.assertTrue(second.resumed)
        self.assertEqual("COMPLETE", second.status)
        self.assertEqual("1", validation_count)
        self.assertEqual("2", reviewer_count)
        self.assertEqual("1", implementer_count)
        self.assertEqual(candidate_before, blocked_record["reviewBlock"]["candidateSha"])
        self.assertEqual(candidate_before, second.pipeline_result.review.reviewed_sha)
        self.assertEqual("Approved", second.pipeline_result.review.decision)

    def test_repeated_review_runtime_decision_unavailable_remains_resumable(self):
        with self.project(implementer_mode="count") as fixture:
            reviewer_counter = fixture.root / "review-count.txt"
            reviewer = fixture.root / "reviewer.py"
            reviewer.write_text(
                "from pathlib import Path\n"
                f"counter = Path(r'{reviewer_counter}')\n"
                "value = int(counter.read_text(encoding='utf-8')) if counter.exists() else 0\n"
                "counter.write_text(str(value + 1), encoding='utf-8')\n"
                "print('Waiting for the background validation run before issuing the final decision.')\n",
                encoding="utf-8",
            )
            fixture.config = self.write_config(
                fixture.root / "project-config.json",
                fixture.repo,
                implementer_mode="count",
                reviewer=f'"{sys.executable}" "{reviewer}"',
            )
            publisher = FakePublisher(fixture.repo)
            service = RunService(pipeline=RunPipeline(publisher=publisher))
            first = service.run(RunRequest(fixture.repo, "Repeated interrupted review output", None, fixture.config))
            second = service.run(RunRequest(fixture.repo, "Repeated interrupted review output", None, fixture.config))
            status = StatusService().run(StatusRequest(fixture.repo, fixture.config))
            reviewer_count = reviewer_counter.read_text(encoding="utf-8")
            codes = self.codes(second)

        self.assertEqual("REVIEW_BLOCKED", first.status)
        self.assertTrue(second.resumed)
        self.assertEqual("REVIEW_BLOCKED", second.status)
        self.assertEqual("2", reviewer_count)
        self.assertEqual("True", status.workflow.evidence["resumable"])
        self.assertEqual("review", status.workflow.evidence["resume_stage"])
        self.assertNotIn("ACTIVE_WORKTREE_PRESENT", codes)
        self.assertNotIn("FEATURE_BRANCH_EXISTS", codes)
        self.assertNotIn("WORKTREE_PATH_EXISTS", codes)
        self.assertNotIn("CONFLICTING_WORKTREE", codes)

    def test_review_runtime_unavailable_changes_requested_resume_preserves_review_stage_before_fix(self):
        with self.project() as fixture:
            implementer_counter = fixture.root / "implementer-count.txt"
            reviewer_counter = fixture.root / "review-count.txt"
            implementer = fixture.root / "implementer.py"
            reviewer = fixture.root / "reviewer.py"
            implementer.write_text(
                "from pathlib import Path\n"
                f"counter = Path(r'{implementer_counter}')\n"
                "value = int(counter.read_text(encoding='utf-8')) if counter.exists() else 0\n"
                "counter.write_text(str(value + 1), encoding='utf-8')\n"
                "Path(f'candidate-{value + 1}.txt').write_text(str(value + 1), encoding='utf-8')\n",
                encoding="utf-8",
            )
            reviewer.write_text(
                "from pathlib import Path\n"
                f"counter = Path(r'{reviewer_counter}')\n"
                "value = int(counter.read_text(encoding='utf-8')) if counter.exists() else 0\n"
                "counter.write_text(str(value + 1), encoding='utf-8')\n"
                "if value == 0:\n"
                "    print('Waiting for the background validation run before issuing the final decision.')\n"
                "elif value == 1:\n"
                "    print('Changes Requested')\n"
                "else:\n"
                "    print('Approved')\n",
                encoding="utf-8",
            )
            fixture.config = self.write_config(
                fixture.root / "project-config.json",
                fixture.repo,
                implementer=f'"{sys.executable}" "{implementer}"',
                reviewer=f'"{sys.executable}" "{reviewer}"',
            )
            publisher = FakePublisher(fixture.repo)
            service = RunService(pipeline=RunPipeline(publisher=publisher))
            first = service.run(RunRequest(fixture.repo, "Runtime unavailable then changes", None, fixture.config))
            second = service.run(RunRequest(fixture.repo, "Runtime unavailable then changes", None, fixture.config))
            stages = [(stage.id, stage.status) for stage in second.pipeline_result.stages]
            implementer_count = implementer_counter.read_text(encoding="utf-8")
            reviewer_count = reviewer_counter.read_text(encoding="utf-8")

        self.assertEqual("REVIEW_BLOCKED", first.status)
        self.assertTrue(second.resumed)
        self.assertEqual("COMPLETE", second.status)
        self.assertEqual("2", implementer_count)
        self.assertEqual("3", reviewer_count)
        self.assertLess(stages.index(("review", "Changes Requested")), stages.index(("implementer", "READY_FOR_VALIDATION")))
        self.assertIn(("review", "Approved"), stages)

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

    def test_cleanup_resume_treats_unregistered_expected_worktree_as_already_removed(self):
        with self.project() as fixture:
            failed_once = {"value": False}
            original_run = run_pipeline._run

            def fail_first_local_branch_delete(args, cwd):
                if args[:3] == ("git", "branch", "-d") and not failed_once["value"]:
                    failed_once["value"] = True
                    return subprocess.CompletedProcess(args, 1, "", "synthetic branch delete interruption")
                return original_run(args, cwd)

            with mock.patch("ados.run_pipeline._run", side_effect=fail_first_local_branch_delete):
                first = RunService(pipeline=RunPipeline(publisher=FakePublisher(fixture.repo))).run(RunRequest(fixture.repo, "Retry after removed worktree", None, fixture.config))
            worktree_path = Path(first.plan.feature_worktree)
            worktree_path.mkdir()
            (worktree_path / "leftover.txt").write_text("not a registered git worktree\n", encoding="utf-8")

            second = RunService(pipeline=RunPipeline(publisher=FakePublisher(fixture.repo))).run(RunRequest(fixture.repo, "Retry after removed worktree", None, fixture.config))
            branch_list = self.git(fixture.repo, "branch", "--list", "codex/001-retry-after-removed-worktree").stdout
            worktrees = self.git(fixture.repo, "worktree", "list", "--porcelain").stdout

        self.assertEqual("CLEANUP_INCOMPLETE", first.status)
        self.assertIn("LOCAL_BRANCH_DELETE_FAILED", {violation.code for violation in first.pipeline_result.violations})
        self.assertTrue(second.resumed)
        self.assertEqual("COMPLETE", second.status)
        self.assertEqual("", branch_list.strip())
        self.assertNotIn(str(worktree_path), worktrees)

    def test_cleanup_resume_blocks_when_expected_path_registered_to_another_branch(self):
        with self.project() as fixture:
            failed_once = {"value": False}
            original_run = run_pipeline._run

            def fail_first_local_branch_delete(args, cwd):
                if args[:3] == ("git", "branch", "-d") and not failed_once["value"]:
                    failed_once["value"] = True
                    return subprocess.CompletedProcess(args, 1, "", "synthetic branch delete interruption")
                return original_run(args, cwd)

            with mock.patch("ados.run_pipeline._run", side_effect=fail_first_local_branch_delete):
                first = RunService(pipeline=RunPipeline(publisher=FakePublisher(fixture.repo))).run(RunRequest(fixture.repo, "Path reused by other branch", None, fixture.config))
            worktree_path = Path(first.plan.feature_worktree)
            self.git(fixture.repo, "worktree", "add", "-b", "codex/unrelated-cleanup-path", str(worktree_path), "HEAD")
            try:
                second = RunService(pipeline=RunPipeline(publisher=FakePublisher(fixture.repo))).run(RunRequest(fixture.repo, "Path reused by other branch", None, fixture.config))
            finally:
                self.git(fixture.repo, "worktree", "remove", str(worktree_path))

        self.assertEqual("CLEANUP_INCOMPLETE", first.status)
        self.assertTrue(second.resumed)
        self.assertEqual("CLEANUP_INCOMPLETE", second.status)
        self.assertIn("WORKTREE_BRANCH_MISMATCH", {violation.code for violation in second.pipeline_result.violations})

    def test_cleanup_resume_blocks_when_feature_branch_owned_by_another_worktree(self):
        with self.project() as fixture:
            failed_once = {"value": False}
            original_run = run_pipeline._run

            def fail_first_local_branch_delete(args, cwd):
                if args[:3] == ("git", "branch", "-d") and not failed_once["value"]:
                    failed_once["value"] = True
                    return subprocess.CompletedProcess(args, 1, "", "synthetic branch delete interruption")
                return original_run(args, cwd)

            with mock.patch("ados.run_pipeline._run", side_effect=fail_first_local_branch_delete):
                first = RunService(pipeline=RunPipeline(publisher=FakePublisher(fixture.repo))).run(RunRequest(fixture.repo, "Branch owned elsewhere", None, fixture.config))
            other_worktree = fixture.root / "other-owner"
            self.git(fixture.repo, "worktree", "add", str(other_worktree), "codex/001-branch-owned-elsewhere")
            try:
                second = RunService(pipeline=RunPipeline(publisher=FakePublisher(fixture.repo))).run(RunRequest(fixture.repo, "Branch owned elsewhere", None, fixture.config))
            finally:
                self.git(fixture.repo, "worktree", "remove", str(other_worktree))

        self.assertEqual("CLEANUP_INCOMPLETE", first.status)
        self.assertFalse(second.resumed)
        self.assertEqual("BLOCKED", second.status)
        self.assertIn("CONFLICTING_WORKTREE", self.codes(second))

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

    def test_legacy_unclassified_changes_requested_block_is_resumable_without_side_effect_artifacts(self):
        with self.project(implementer_mode="count") as fixture:
            record_path, record, reviewed_candidate = self.create_review_changes_requested_blocked_run(fixture, "Legacy unclassified review recovery", 1)
            record["reviewBlock"]["reasonCode"] = "REVIEW_BLOCK_UNCLASSIFIED"
            record["reviewBlock"]["resumeStage"] = ""
            record["reviewBlock"].pop("blockCause", None)
            record_path.write_text(json.dumps(record, indent=2, sort_keys=True), encoding="utf-8")
            worktree = Path(record["featureWorktree"])
            (worktree / "manual-review-fix.txt").write_text("fixed\n", encoding="utf-8")
            self.git(worktree, "add", "manual-review-fix.txt")
            self.git(worktree, "commit", "-m", "manual review recovery")
            adopted_candidate = self.head(worktree)
            status = StatusService().run(StatusRequest(fixture.repo, fixture.config))
            result = RunService(pipeline=RunPipeline(publisher=FakePublisher(fixture.repo))).run(RunRequest(fixture.repo, "Legacy unclassified review recovery", 1, fixture.config))
            codes = self.codes(result)

        self.assertEqual("True", status.workflow.evidence["resumable"])
        self.assertEqual("REVIEW_CHANGES_REQUESTED", status.workflow.evidence["review_block_reason"])
        self.assertEqual("implementation_recovery", status.workflow.evidence["resume_stage"])
        self.assertTrue(result.resumed)
        self.assertEqual("COMPLETE", result.status)
        self.assertNotIn("ACTIVE_WORKTREE_PRESENT", codes)
        self.assertNotIn("FEATURE_BRANCH_EXISTS", codes)
        self.assertNotIn("WORKTREE_PATH_EXISTS", codes)
        self.assertNotIn("CONFLICTING_WORKTREE", codes)
        self.assertEqual(reviewed_candidate, result.pipeline_result.run_record["reviewChangesRecoveryAdoption"]["previousReviewedCandidateSha"])
        self.assertEqual(adopted_candidate, result.pipeline_result.validation.head_after)
        self.assertEqual(adopted_candidate, result.pipeline_result.review.reviewed_sha)
        self.assertEqual("Approved", result.pipeline_result.review.decision)

    def test_legacy_unclassified_changes_requested_block_with_generated_artifacts_is_not_resumable(self):
        with self.project(implementer_mode="count") as fixture:
            record_path, record, _reviewed_candidate = self.create_review_changes_requested_blocked_run(fixture, "Legacy generated artifact review recovery", 1)
            record["reviewBlock"]["reasonCode"] = "REVIEW_BLOCK_UNCLASSIFIED"
            record["reviewBlock"]["resumeStage"] = ""
            record["reviewBlock"].pop("blockCause", None)
            record_path.write_text(json.dumps(record, indent=2, sort_keys=True), encoding="utf-8")
            record_path.with_name("review-generated-artifacts.json").write_text(json.dumps({"artifacts": [{"source": "specs/001/review.md"}]}), encoding="utf-8")
            worktree = Path(record["featureWorktree"])
            (worktree / "manual-review-fix.txt").write_text("fixed\n", encoding="utf-8")
            self.git(worktree, "add", "manual-review-fix.txt")
            self.git(worktree, "commit", "-m", "manual review recovery")
            status = StatusService().run(StatusRequest(fixture.repo, fixture.config))
            result = RunService(pipeline=RunPipeline(publisher=FakePublisher(fixture.repo))).run(RunRequest(fixture.repo, "Legacy generated artifact review recovery", 1, fixture.config, dry_run=True))

        self.assertEqual("False", status.workflow.evidence["resumable"])
        self.assertEqual("REVIEW_BLOCK_UNCLASSIFIED", status.workflow.evidence["review_block_reason"])
        self.assertEqual("", status.workflow.evidence["resume_stage"])
        self.assertEqual("BLOCKED", result.status)
        self.assertFalse(result.resumed)
        self.assertIn("ACTIVE_WORKTREE_PRESENT", self.codes(result))

    def test_legacy_unclassified_changes_requested_block_with_review_artifact_directory_is_not_resumable(self):
        with self.project(implementer_mode="count") as fixture:
            record_path, record, _reviewed_candidate = self.create_review_changes_requested_blocked_run(fixture, "Legacy artifact directory review recovery", 1)
            record["reviewBlock"]["reasonCode"] = "REVIEW_BLOCK_UNCLASSIFIED"
            record["reviewBlock"]["resumeStage"] = ""
            record["reviewBlock"].pop("blockCause", None)
            record_path.write_text(json.dumps(record, indent=2, sort_keys=True), encoding="utf-8")
            artifacts = record_path.with_name("review-artifacts")
            artifacts.mkdir()
            (artifacts / "review.md").write_text("generated review", encoding="utf-8")
            worktree = Path(record["featureWorktree"])
            (worktree / "manual-review-fix.txt").write_text("fixed\n", encoding="utf-8")
            self.git(worktree, "add", "manual-review-fix.txt")
            self.git(worktree, "commit", "-m", "manual review recovery")
            status = StatusService().run(StatusRequest(fixture.repo, fixture.config))
            result = RunService(pipeline=RunPipeline(publisher=FakePublisher(fixture.repo))).run(RunRequest(fixture.repo, "Legacy artifact directory review recovery", 1, fixture.config, dry_run=True))

        self.assertEqual("False", status.workflow.evidence["resumable"])
        self.assertEqual("", status.workflow.evidence["resume_stage"])
        self.assertEqual("BLOCKED", result.status)
        self.assertFalse(result.resumed)

    def test_legacy_unclassified_changes_requested_block_with_sha_mismatch_is_not_resumable(self):
        with self.project(implementer_mode="count") as fixture:
            record_path, record, _reviewed_candidate = self.create_review_changes_requested_blocked_run(fixture, "Legacy sha mismatch review recovery", 1)
            record["reviewBlock"]["reasonCode"] = "REVIEW_BLOCK_UNCLASSIFIED"
            record["reviewBlock"]["resumeStage"] = ""
            record["reviewBlock"]["candidateSha"] = "0" * 40
            record["reviewBlock"].pop("blockCause", None)
            record_path.write_text(json.dumps(record, indent=2, sort_keys=True), encoding="utf-8")
            worktree = Path(record["featureWorktree"])
            (worktree / "manual-review-fix.txt").write_text("fixed\n", encoding="utf-8")
            self.git(worktree, "add", "manual-review-fix.txt")
            self.git(worktree, "commit", "-m", "manual review recovery")
            status = StatusService().run(StatusRequest(fixture.repo, fixture.config))
            result = RunService(pipeline=RunPipeline(publisher=FakePublisher(fixture.repo))).run(RunRequest(fixture.repo, "Legacy sha mismatch review recovery", 1, fixture.config, dry_run=True))

        self.assertEqual("False", status.workflow.evidence["resumable"])
        self.assertEqual("BLOCKED", result.status)
        self.assertFalse(result.resumed)

    def test_legacy_unclassified_changes_requested_block_with_reason_codes_is_not_resumable(self):
        with self.project(implementer_mode="count") as fixture:
            record_path, record, _reviewed_candidate = self.create_review_changes_requested_blocked_run(fixture, "Legacy reason codes review recovery", 1)
            record["reviewBlock"]["reasonCode"] = "REVIEW_BLOCK_UNCLASSIFIED"
            record["reviewBlock"]["reasonCodes"] = ["REVIEW_SIDE_EFFECT_UNEXPECTED"]
            record["reviewBlock"]["resumeStage"] = ""
            record["reviewBlock"].pop("blockCause", None)
            record_path.write_text(json.dumps(record, indent=2, sort_keys=True), encoding="utf-8")
            worktree = Path(record["featureWorktree"])
            (worktree / "manual-review-fix.txt").write_text("fixed\n", encoding="utf-8")
            self.git(worktree, "add", "manual-review-fix.txt")
            self.git(worktree, "commit", "-m", "manual review recovery")
            status = StatusService().run(StatusRequest(fixture.repo, fixture.config))
            result = RunService(pipeline=RunPipeline(publisher=FakePublisher(fixture.repo))).run(RunRequest(fixture.repo, "Legacy reason codes review recovery", 1, fixture.config, dry_run=True))

        self.assertEqual("False", status.workflow.evidence["resumable"])
        self.assertEqual("BLOCKED", result.status)
        self.assertFalse(result.resumed)

    def test_legacy_unclassified_changes_requested_block_with_nonzero_exit_is_not_resumable(self):
        with self.project(implementer_mode="count") as fixture:
            record_path, record, _reviewed_candidate = self.create_review_changes_requested_blocked_run(fixture, "Legacy nonzero exit review recovery", 1)
            record["reviewBlock"]["reasonCode"] = "REVIEW_BLOCK_UNCLASSIFIED"
            record["reviewBlock"]["exitCode"] = 7
            record["reviewBlock"]["resumeStage"] = ""
            record["reviewBlock"].pop("blockCause", None)
            record_path.write_text(json.dumps(record, indent=2, sort_keys=True), encoding="utf-8")
            worktree = Path(record["featureWorktree"])
            (worktree / "manual-review-fix.txt").write_text("fixed\n", encoding="utf-8")
            self.git(worktree, "add", "manual-review-fix.txt")
            self.git(worktree, "commit", "-m", "manual review recovery")
            status = StatusService().run(StatusRequest(fixture.repo, fixture.config))
            result = RunService(pipeline=RunPipeline(publisher=FakePublisher(fixture.repo))).run(RunRequest(fixture.repo, "Legacy nonzero exit review recovery", 1, fixture.config, dry_run=True))

        self.assertEqual("False", status.workflow.evidence["resumable"])
        self.assertEqual("BLOCKED", result.status)
        self.assertFalse(result.resumed)

    def test_parser_failed_changes_requested_output_is_resumable(self):
        with self.project(implementer_mode="count") as fixture:
            record_path, record, reviewed_candidate = self.create_review_changes_requested_blocked_run(fixture, "Parser failed review recovery", 1)
            record["reviewBlock"].update(
                {
                    "status": "BLOCK",
                    "decision": "Unavailable",
                    "reasonCode": "REVIEW_DECISION_UNAVAILABLE",
                    "reasonCodes": ["REVIEW_DECISION_UNAVAILABLE"],
                    "blockCause": "review_runtime",
                    "resumeStage": "",
                }
            )
            record_path.write_text(json.dumps(record, indent=2, sort_keys=True), encoding="utf-8")
            record_path.with_name("review-runtime.json").write_text(
                json.dumps(
                    {
                        "status": "BLOCK",
                        "decision": "Unavailable",
                        "reviewed_sha": reviewed_candidate,
                        "exit_code": 0,
                        "stdout": "Changes Requested -- the candidate needs a recovery fix.",
                        "stderr": "",
                        "violations": [{"code": "REVIEW_DECISION_UNAVAILABLE", "message": "reviewer output did not contain a supported decision", "evidence": {}}],
                    },
                    indent=2,
                    sort_keys=True,
                ),
                encoding="utf-8",
            )
            worktree = Path(record["featureWorktree"])
            (worktree / "manual-parser-fix.txt").write_text("fixed\n", encoding="utf-8")
            self.git(worktree, "add", "manual-parser-fix.txt")
            self.git(worktree, "commit", "-m", "manual parser recovery")
            adopted_candidate = self.head(worktree)
            status = StatusService().run(StatusRequest(fixture.repo, fixture.config))
            result = RunService(pipeline=RunPipeline(publisher=FakePublisher(fixture.repo))).run(RunRequest(fixture.repo, "Parser failed review recovery", 1, fixture.config))
            codes = self.codes(result)

        self.assertEqual("True", status.workflow.evidence["resumable"])
        self.assertEqual("REVIEW_DECISION_UNAVAILABLE", status.workflow.evidence["review_block_reason"])
        self.assertEqual("implementation_recovery", status.workflow.evidence["resume_stage"])
        self.assertTrue(result.resumed)
        self.assertEqual("COMPLETE", result.status)
        self.assertNotIn("ACTIVE_WORKTREE_PRESENT", codes)
        self.assertNotIn("FEATURE_BRANCH_EXISTS", codes)
        self.assertNotIn("WORKTREE_PATH_EXISTS", codes)
        self.assertNotIn("CONFLICTING_WORKTREE", codes)
        self.assertEqual(reviewed_candidate, result.pipeline_result.run_record["reviewChangesRecoveryAdoption"]["previousReviewedCandidateSha"])
        self.assertEqual(adopted_candidate, result.pipeline_result.validation.head_after)
        self.assertEqual(adopted_candidate, result.pipeline_result.review.reviewed_sha)

    def test_review_decision_heading_parser_failed_run_resumes_without_new_run_conflicts_and_delivers_body(self):
        with self.project() as fixture:
            implementer_prompt = fixture.root / "implementer-prompt.txt"
            implementer = fixture.root / "implementer.py"
            implementer.write_text(
                "from pathlib import Path\n"
                "import sys\n"
                "prompt = sys.stdin.read()\n"
                f"Path(r'{implementer_prompt}').write_text(prompt, encoding='utf-8')\n"
                "assert 'validation_recovery_implementer is incorrectly classified as blocked' in prompt\n"
                "Path('review-fix.txt').write_text('fixed review finding', encoding='utf-8')\n",
                encoding="utf-8",
            )
            reviewer_counter = fixture.root / "review-count.txt"
            reviewer = fixture.root / "reviewer.py"
            reviewer.write_text(
                "from pathlib import Path\n"
                f"counter = Path(r'{reviewer_counter}')\n"
                "value = int(counter.read_text(encoding='utf-8')) if counter.exists() else 0\n"
                "counter.write_text(str(value + 1), encoding='utf-8')\n"
                "if value == 0:\n"
                "    print('# Review Decision: Changes Requested')\n"
                "    print('\\nBlocking finding:\\n\\nsrc/.../LiveAgentWorkVisualization.ts\\n\\nvalidation_recovery_implementer is incorrectly classified as blocked.')\n"
                "else:\n"
                "    print('# Review Decision: Approved')\n",
                encoding="utf-8",
            )
            fixture.config = self.write_config(
                fixture.root / "project-config.json",
                fixture.repo,
                implementer=f'"{sys.executable}" "{implementer}"',
                reviewer=f'"{sys.executable}" "{reviewer}"',
            )
            record_path, record, reviewed_candidate = self.create_review_changes_requested_blocked_run(fixture, "Review decision heading recovery", 1)
            record["implementer"] = f'"{sys.executable}" "{implementer}"'
            record["reviewer"] = f'"{sys.executable}" "{reviewer}"'
            record["reviewBlock"].update(
                {
                    "status": "BLOCK",
                    "decision": "Unavailable",
                    "reasonCode": "REVIEW_DECISION_UNAVAILABLE",
                    "reasonCodes": ["REVIEW_DECISION_UNAVAILABLE"],
                    "blockCause": "review_runtime",
                    "resumeStage": "",
                }
            )
            record_path.write_text(json.dumps(record, indent=2, sort_keys=True), encoding="utf-8")
            historical_stdout = (
                "# Review Decision: Changes Requested\n\n"
                "Blocking finding:\n\n"
                "src/.../LiveAgentWorkVisualization.ts\n\n"
                "validation_recovery_implementer is incorrectly classified as blocked.\n"
            )
            record_path.with_name("review-runtime.json").write_text(
                json.dumps(
                    {
                        "status": "BLOCK",
                        "decision": "Unavailable",
                        "reviewed_sha": reviewed_candidate,
                        "exit_code": 0,
                        "stdout": historical_stdout,
                        "stderr": "",
                        "violations": [{"code": "REVIEW_DECISION_UNAVAILABLE", "message": "reviewer output did not contain a supported decision", "evidence": {}}],
                    },
                    indent=2,
                    sort_keys=True,
                ),
                encoding="utf-8",
            )

            status = StatusService().run(StatusRequest(fixture.repo, fixture.config))
            result = RunService(pipeline=RunPipeline(publisher=FakePublisher(fixture.repo))).run(RunRequest(fixture.repo, "Review decision heading recovery", 1, fixture.config))
            codes = self.codes(result)
            handoff = implementer_prompt.read_text(encoding="utf-8")
            reviewer_count = reviewer_counter.read_text(encoding="utf-8")

        self.assertEqual("True", status.workflow.evidence["resumable"])
        self.assertEqual("implementation_recovery", status.workflow.evidence["resume_stage"])
        self.assertTrue(result.resumed)
        self.assertEqual("COMPLETE", result.status)
        self.assertNotIn("ACTIVE_WORKTREE_PRESENT", codes)
        self.assertNotIn("FEATURE_BRANCH_EXISTS", codes)
        self.assertNotIn("WORKTREE_PATH_EXISTS", codes)
        self.assertNotIn("CONFLICTING_WORKTREE", codes)
        self.assertIn("Independent review Changes Requested context:", handoff)
        self.assertIn("validation_recovery_implementer is incorrectly classified as blocked", handoff)
        self.assertEqual("2", reviewer_count)
        self.assertNotEqual(reviewed_candidate, result.pipeline_result.validation.head_after)
        self.assertEqual(result.pipeline_result.validation.head_after, result.pipeline_result.review.reviewed_sha)

    def test_parser_failed_ambiguous_output_is_not_resumable(self):
        with self.project(implementer_mode="count") as fixture:
            record_path, record, reviewed_candidate = self.create_review_changes_requested_blocked_run(fixture, "Parser ambiguous review recovery", 1)
            record["reviewBlock"].update(
                {
                    "status": "BLOCK",
                    "decision": "Unavailable",
                    "reasonCode": "REVIEW_DECISION_UNAVAILABLE",
                    "reasonCodes": ["REVIEW_DECISION_UNAVAILABLE"],
                    "blockCause": "review_runtime",
                    "resumeStage": "",
                }
            )
            record_path.write_text(json.dumps(record, indent=2, sort_keys=True), encoding="utf-8")
            record_path.with_name("review-runtime.json").write_text(
                json.dumps(
                    {
                        "status": "BLOCK",
                        "decision": "Unavailable",
                        "reviewed_sha": reviewed_candidate,
                        "exit_code": 0,
                        "stdout": "The previous review was Approved, but this candidate has blockers.",
                        "stderr": "",
                        "violations": [{"code": "REVIEW_DECISION_UNAVAILABLE", "message": "reviewer output did not contain a supported decision", "evidence": {}}],
                    },
                    indent=2,
                    sort_keys=True,
                ),
                encoding="utf-8",
            )
            worktree = Path(record["featureWorktree"])
            (worktree / "manual-parser-fix.txt").write_text("fixed\n", encoding="utf-8")
            self.git(worktree, "add", "manual-parser-fix.txt")
            self.git(worktree, "commit", "-m", "manual parser recovery")
            status = StatusService().run(StatusRequest(fixture.repo, fixture.config))
            result = RunService(pipeline=RunPipeline(publisher=FakePublisher(fixture.repo))).run(RunRequest(fixture.repo, "Parser ambiguous review recovery", 1, fixture.config, dry_run=True))

        self.assertEqual("False", status.workflow.evidence["resumable"])
        self.assertEqual("", status.workflow.evidence["resume_stage"])
        self.assertEqual("BLOCKED", result.status)
        self.assertFalse(result.resumed)
        self.assertIn("ACTIVE_WORKTREE_PRESENT", self.codes(result))

    def test_review_side_effect_restore_retries_review_and_publishes(self):
        with self.project() as fixture:
            counter = fixture.root / "review-count.txt"
            reviewer = fixture.root / "reviewer.py"
            reviewer.write_text(
                "from pathlib import Path\n"
                f"counter = Path(r'{counter}')\n"
                "value = int(counter.read_text(encoding='utf-8')) if counter.exists() else 0\n"
                "counter.write_text(str(value + 1), encoding='utf-8')\n"
                "if value == 0:\n"
                "    Path('implementation.txt').write_text('reviewer side effect', encoding='utf-8')\n"
                "print('Approved')\n",
                encoding="utf-8",
            )
            fixture.config = self.write_config(
                fixture.root / "project-config.json",
                fixture.repo,
                reviewer=f'"{sys.executable}" "{reviewer}"',
            )
            result = RunService(pipeline=RunPipeline(publisher=FakePublisher(fixture.repo))).run(RunRequest(fixture.repo, "Recover review side effect", 1, fixture.config))
            stages = [(stage.id, stage.status) for stage in result.pipeline_result.stages]
            attempts = result.pipeline_result.run_record.get("reviewSideEffectRecoveryAttempts", [])
            review_count = counter.read_text(encoding="utf-8")

        self.assertEqual("COMPLETE", result.status)
        self.assertIn(("review_side_effect_recovery", "PASS"), stages)
        self.assertEqual("2", review_count)
        self.assertEqual(1, len(attempts))
        self.assertEqual("RESTORED", attempts[0]["status"])
        self.assertEqual(["implementation.txt"], attempts[0]["dirtyTrackedPaths"])

    def test_existing_review_side_effect_block_resumes_restores_and_retries_review(self):
        with self.project() as fixture:
            reviewer = fixture.root / "reviewer.py"
            reviewer.write_text("print('Approved')\n", encoding="utf-8")
            fixture.config = self.write_config(
                fixture.root / "project-config.json",
                fixture.repo,
                reviewer=f'"{sys.executable}" "{reviewer}"',
            )
            record_path, record = self.create_durable_run(fixture, "Resume review side effect", 1, "REVIEW_BLOCKED")
            worktree = Path(record["featureWorktree"])
            spec_dir = worktree / "specs" / "001-resume-review-side-effect"
            spec_dir.mkdir(parents=True)
            (spec_dir / "spec.md").write_text("# Resume review side effect\n", encoding="utf-8")
            (worktree / "implementation.txt").write_text("candidate\n", encoding="utf-8")
            self.git(worktree, "add", "specs", "implementation.txt")
            self.git(worktree, "commit", "-m", "spec 001: Resume review side effect")
            candidate_sha = self.head(worktree)
            record["status"] = "REVIEW_BLOCKED"
            record["nextStage"] = "review"
            record["reviewBlock"] = {
                "status": "PASS",
                "decision": "Approved",
                "reasonCode": "REVIEW_SIDE_EFFECT_DIRTY_WORKTREE",
                "reasonCodes": ["REVIEW_SIDE_EFFECT_DIRTY_WORKTREE"],
                "blockCause": "review_side_effect",
                "transient": False,
                "resumeStage": "review",
                "reviewer": record["reviewer"],
                "candidateSha": candidate_sha,
                "validatedSha": candidate_sha,
                "baseSha": record["authoritativeBaseSha"],
                "reviewedSha": candidate_sha,
                "exitCode": 0,
                "timedOut": False,
            }
            record_path.write_text(json.dumps(record, indent=2, sort_keys=True), encoding="utf-8")
            record_path.with_name("candidate.json").write_text(json.dumps({"status": "COMMITTED", "candidate_sha": candidate_sha, "changed_files": ["specs/001-resume-review-side-effect/spec.md", "implementation.txt"]}, indent=2, sort_keys=True), encoding="utf-8")
            record_path.with_name("validation-runtime.json").write_text(json.dumps({"status": "PASS", "head_before": candidate_sha, "head_after": candidate_sha, "commands": [], "violations": []}, indent=2, sort_keys=True), encoding="utf-8")
            record_path.with_name("review-runtime.json").write_text(json.dumps({"status": "PASS", "decision": "Approved", "reviewed_sha": candidate_sha, "exit_code": 0, "stdout": "Approved", "stderr": "", "violations": []}, indent=2, sort_keys=True), encoding="utf-8")
            (worktree / "implementation.txt").write_text("reviewer side effect\n", encoding="utf-8")
            status = StatusService().run(StatusRequest(fixture.repo, fixture.config))
            result = RunService(pipeline=RunPipeline(publisher=FakePublisher(fixture.repo))).run(RunRequest(fixture.repo, "Resume review side effect", 1, fixture.config))
            stages = [(stage.id, stage.status) for stage in result.pipeline_result.stages]

        self.assertEqual("True", status.workflow.evidence["resumable"])
        self.assertEqual("review", status.workflow.evidence["resume_stage"])
        self.assertTrue(result.resumed)
        self.assertEqual("COMPLETE", result.status)
        self.assertIn(("review_side_effect_recovery", "PASS"), stages)
        self.assertNotIn("ACTIVE_WORKTREE_PRESENT", self.codes(result))

    def test_review_side_effect_recovery_removes_untracked_files(self):
        with self.project() as fixture:
            counter = fixture.root / "review-count.txt"
            reviewer = fixture.root / "reviewer.py"
            reviewer.write_text(
                "from pathlib import Path\n"
                f"counter = Path(r'{counter}')\n"
                "value = int(counter.read_text(encoding='utf-8')) if counter.exists() else 0\n"
                "counter.write_text(str(value + 1), encoding='utf-8')\n"
                "if value == 0:\n"
                "    Path('review-output.txt').write_text('reviewer side effect', encoding='utf-8')\n"
                "print('Approved')\n",
                encoding="utf-8",
            )
            fixture.config = self.write_config(
                fixture.root / "project-config.json",
                fixture.repo,
                reviewer=f'"{sys.executable}" "{reviewer}"',
            )
            result = RunService(pipeline=RunPipeline(publisher=FakePublisher(fixture.repo))).run(RunRequest(fixture.repo, "Recover untracked review side effect", 1, fixture.config))
            attempts = result.pipeline_result.run_record.get("reviewSideEffectRecoveryAttempts", [])
            review_count = counter.read_text(encoding="utf-8")

        self.assertEqual("COMPLETE", result.status)
        self.assertEqual("2", review_count)
        self.assertEqual(1, len(attempts))
        self.assertEqual(["review-output.txt"], attempts[0]["untrackedPaths"])

    def test_review_side_effect_sha_mismatch_is_not_resumable(self):
        with self.project() as fixture:
            record_path, record = self.create_durable_run(fixture, "Unsafe review side effect", 1, "REVIEW_BLOCKED")
            worktree = Path(record["featureWorktree"])
            (worktree / "implementation.txt").write_text("candidate\n", encoding="utf-8")
            self.git(worktree, "add", "implementation.txt")
            self.git(worktree, "commit", "-m", "spec 001: Unsafe review side effect")
            candidate_sha = self.head(worktree)
            stale_sha = record["authoritativeBaseSha"]
            record["status"] = "REVIEW_BLOCKED"
            record["nextStage"] = "review"
            record["reviewBlock"] = {
                "status": "PASS",
                "decision": "Approved",
                "reasonCode": "REVIEW_SIDE_EFFECT_DIRTY_WORKTREE",
                "reasonCodes": ["REVIEW_SIDE_EFFECT_DIRTY_WORKTREE"],
                "blockCause": "review_side_effect",
                "transient": False,
                "resumeStage": "review",
                "reviewer": record["reviewer"],
                "candidateSha": stale_sha,
                "validatedSha": candidate_sha,
                "baseSha": record["authoritativeBaseSha"],
                "reviewedSha": candidate_sha,
                "exitCode": 0,
                "timedOut": False,
            }
            record_path.write_text(json.dumps(record, indent=2, sort_keys=True), encoding="utf-8")
            record_path.with_name("candidate.json").write_text(json.dumps({"status": "COMMITTED", "candidate_sha": candidate_sha, "changed_files": ["implementation.txt"]}, indent=2, sort_keys=True), encoding="utf-8")
            record_path.with_name("validation-runtime.json").write_text(json.dumps({"status": "PASS", "head_before": candidate_sha, "head_after": candidate_sha, "commands": [], "violations": []}, indent=2, sort_keys=True), encoding="utf-8")
            record_path.with_name("review-runtime.json").write_text(json.dumps({"status": "PASS", "decision": "Approved", "reviewed_sha": candidate_sha, "exit_code": 0, "stdout": "Approved", "stderr": "", "violations": []}, indent=2, sort_keys=True), encoding="utf-8")
            (worktree / "implementation.txt").write_text("reviewer side effect\n", encoding="utf-8")
            status = StatusService().run(StatusRequest(fixture.repo, fixture.config))
            result = RunService(pipeline=RunPipeline(publisher=FakePublisher(fixture.repo))).run(RunRequest(fixture.repo, "Unsafe review side effect", 1, fixture.config, dry_run=True))

        self.assertEqual("False", status.workflow.evidence["resumable"])
        self.assertEqual("", status.workflow.evidence["resume_stage"])
        self.assertEqual("BLOCKED", result.status)
        self.assertFalse(result.resumed)
        self.assertIn("ACTIVE_WORKTREE_PRESENT", self.codes(result))

    def test_repeated_review_side_effect_respects_recovery_bound(self):
        with self.project() as fixture:
            reviewer = fixture.root / "reviewer.py"
            reviewer.write_text(
                "from pathlib import Path\n"
                "Path('review-side-effect.txt').write_text('dirty', encoding='utf-8')\n"
                "print('Approved')\n",
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
            second = RunService(pipeline=RunPipeline(publisher=FakePublisher(fixture.repo))).run(RunRequest(fixture.repo, "Dirty review side effect", 1, fixture.config))
            stages = [(stage.id, stage.status) for stage in first.pipeline_result.stages]

        self.assertEqual("REVIEW_BLOCKED", first.status)
        self.assertIn(("review_side_effect_recovery", "PASS"), stages)
        self.assertEqual("REVIEW_SIDE_EFFECT_RECOVERY_MAX_ROUNDS_EXCEEDED", blocked_record["reviewBlock"]["reasonCode"])
        self.assertEqual("review_side_effect", blocked_record["reviewBlock"]["blockCause"])
        self.assertEqual("True", status.workflow.evidence["resumable"])
        self.assertEqual("review", status.workflow.evidence["resume_stage"])
        self.assertEqual("REVIEW_BLOCKED", second.status)
        self.assertTrue(second.resumed)
        self.assertIn("REVIEW_SIDE_EFFECT_RECOVERY_MAX_ROUNDS_EXCEEDED", {violation.code for violation in second.pipeline_result.violations})
        self.assertNotIn("ACTIVE_WORKTREE_PRESENT", self.codes(second))

    def test_exhausted_review_side_effect_recovery_reopens_clean_newer_head_for_fresh_review(self):
        with self.project() as fixture:
            review_count = fixture.root / "review-count.txt"
            reviewer = fixture.root / "reviewer.py"
            reviewer.write_text(
                "from pathlib import Path\n"
                f"counter = Path(r'{review_count}')\n"
                "value = int(counter.read_text(encoding='utf-8')) if counter.exists() else 0\n"
                "counter.write_text(str(value + 1), encoding='utf-8')\n"
                "print('Approved')\n",
                encoding="utf-8",
            )
            fixture.config = self.write_config(
                fixture.root / "project-config.json",
                fixture.repo,
                reviewer=f'"{sys.executable}" "{reviewer}"',
                review_max_side_effect_recovery_rounds=1,
            )
            record_path, record, old_sha, new_sha = self.create_exhausted_review_side_effect_blocked_run(fixture, "Reopen review side effect", 1)
            original_attempts = list(record["reviewSideEffectRecoveryAttempts"])

            blocked = RunService(pipeline=RunPipeline(publisher=FakePublisher(fixture.repo))).run(RunRequest(fixture.repo, "Reopen review side effect", 1, fixture.config))
            result = RunService(pipeline=RunPipeline(publisher=FakePublisher(fixture.repo))).run(
                RunRequest(fixture.repo, "Reopen review side effect", 1, fixture.config, reopen_review_side_effect_recovery=True)
            )
            final_record = result.pipeline_result.run_record
            stages = [stage.id for stage in result.pipeline_result.stages]
            review_count_value = review_count.read_text(encoding="utf-8")

        self.assertEqual("REVIEW_BLOCKED", blocked.status)
        self.assertIn("REVIEW_SIDE_EFFECT_RECOVERY_REOPEN_REQUIRED", {violation.code for violation in blocked.pipeline_result.violations})
        self.assertEqual("COMPLETE", result.status)
        self.assertTrue(result.resumed)
        self.assertIn("review_side_effect_recovery_reopen", stages)
        self.assertEqual(new_sha, result.pipeline_result.candidate.candidate_sha)
        self.assertEqual(new_sha, result.pipeline_result.validation.head_after)
        self.assertEqual(new_sha, result.pipeline_result.review.reviewed_sha)
        self.assertEqual("1", review_count_value)
        self.assertEqual(original_attempts, final_record["reviewSideEffectRecoveryAttempts"])
        self.assertEqual(1, len(final_record["reviewSideEffectRecoveryReopens"]))
        self.assertEqual(old_sha, final_record["reviewSideEffectRecoveryReopens"][0]["previousReviewedSha"])
        self.assertEqual(new_sha, final_record["reviewSideEffectRecoveryReopens"][0]["adoptedCandidateSha"])
        self.assertIn(f"review-runtime-before-review-side-effect-reopen-{old_sha[:12]}.json", final_record["reviewSideEffectRecoveryReopens"][0]["previousReviewArtifact"])

    def test_exhausted_review_side_effect_reopen_dirty_worktree_fails_closed(self):
        with self.project() as fixture:
            record_path, _record, _old_sha, _new_sha = self.create_exhausted_review_side_effect_blocked_run(fixture, "Dirty reopen review side effect", 1)
            worktree = record_path.parents[3]
            (worktree / "dirty.txt").write_text("unsafe\n", encoding="utf-8")
            result = RunService(pipeline=RunPipeline(publisher=FakePublisher(fixture.repo))).run(
                RunRequest(fixture.repo, "Dirty reopen review side effect", 1, fixture.config, reopen_review_side_effect_recovery=True)
            )
            reopened_record = json.loads(record_path.read_text(encoding="utf-8"))

        self.assertEqual("REVIEW_BLOCKED", result.status)
        self.assertIn("REVIEW_SIDE_EFFECT_RECOVERY_REOPEN_WORKTREE_DIRTY", {violation.code for violation in result.pipeline_result.violations})
        self.assertNotIn("reviewSideEffectRecoveryReopens", reopened_record)

    def test_exhausted_review_side_effect_reopen_wrong_branch_fails_closed(self):
        with self.project() as fixture:
            record_path, _record, _old_sha, _new_sha = self.create_exhausted_review_side_effect_blocked_run(fixture, "Wrong branch reopen review side effect", 1)
            worktree = record_path.parents[3]
            self.git(worktree, "checkout", "-b", "wrong-review-reopen")
            result = RunService(pipeline=RunPipeline(publisher=FakePublisher(fixture.repo))).run(
                RunRequest(fixture.repo, "Wrong branch reopen review side effect", 1, fixture.config, reopen_review_side_effect_recovery=True)
            )

        self.assertEqual("BLOCKED", result.status)
        self.assertFalse(result.resumed)
        self.assertIn("FEATURE_BRANCH_EXISTS", self.codes(result))
        self.assertIn("WORKTREE_PATH_EXISTS", self.codes(result))

    def test_review_side_effect_reopen_rejects_unrelated_review_block(self):
        with self.project() as fixture:
            record_path, record, candidate_sha = self.create_review_changes_requested_blocked_run(fixture, "Unrelated review block reopen", 1)
            result = RunService(pipeline=RunPipeline(publisher=FakePublisher(fixture.repo))).run(
                RunRequest(fixture.repo, "Unrelated review block reopen", 1, fixture.config, reopen_review_side_effect_recovery=True)
            )
            unchanged = json.loads(record_path.read_text(encoding="utf-8"))

        self.assertEqual("REVIEW_BLOCKED", result.status)
        self.assertTrue(result.resumed)
        self.assertIn("review_side_effect_recovery_reopen", [stage.id for stage in result.pipeline_result.stages])
        self.assertIn("REVIEW_SIDE_EFFECT_RECOVERY_REOPEN_BLOCK_CAUSE_UNSAFE", {violation.code for violation in result.pipeline_result.violations})
        self.assertEqual(candidate_sha, unchanged["reviewBlock"]["candidateSha"])
        self.assertNotIn("reviewSideEffectRecoveryReopens", unchanged)

    def test_review_side_effect_reopen_already_used_for_same_head_fails_closed(self):
        with self.project() as fixture:
            record_path, record, old_sha, new_sha = self.create_exhausted_review_side_effect_blocked_run(fixture, "Repeat reopen review side effect", 1)
            record["reviewSideEffectRecoveryReopens"] = [{"status": "REOPENED", "adoptedCandidateSha": new_sha}]
            record_path.write_text(json.dumps(record, indent=2, sort_keys=True), encoding="utf-8")
            result = RunService(pipeline=RunPipeline(publisher=FakePublisher(fixture.repo))).run(
                RunRequest(fixture.repo, "Repeat reopen review side effect", 1, fixture.config, reopen_review_side_effect_recovery=True)
            )
            reopened_record = json.loads(record_path.read_text(encoding="utf-8"))

        self.assertEqual("REVIEW_BLOCKED", result.status)
        self.assertIn("REVIEW_SIDE_EFFECT_RECOVERY_REOPEN_ALREADY_USED", {violation.code for violation in result.pipeline_result.violations})
        self.assertEqual([{"adoptedCandidateSha": new_sha, "status": "REOPENED"}], reopened_record["reviewSideEffectRecoveryReopens"])

    def test_review_side_effect_budget_is_scoped_to_review_cycle(self):
        with self.project() as fixture:
            fixture.config = self.write_config(
                fixture.root / "project-config.json",
                fixture.repo,
                review_max_side_effect_recovery_rounds=1,
            )
            config = load_project_config(fixture.config)
            run_dir = fixture.repo / ".agent-workflow" / "runs" / "001-cycle-scoped-review-side-effect"
            run_dir.mkdir(parents=True)
            record_path = run_dir / "ados-run.json"
            base_sha = self.head(fixture.repo)
            record = {
                "runId": "cycle-scoped-review-side-effect",
                "status": "REVIEW_BLOCKED",
                "nextStage": "review",
                "featureBranch": "main",
                "featureWorktree": str(fixture.repo),
                "authoritativeBaseSha": base_sha,
                "reviewer": "reviewer",
            }
            pipeline = RunPipeline(publisher=FakePublisher(fixture.repo))

            (fixture.repo / "implementation.txt").write_text("candidate 1\n", encoding="utf-8")
            self.git(fixture.repo, "add", "implementation.txt")
            self.git(fixture.repo, "commit", "-m", "candidate 1")
            candidate_1 = self.head(fixture.repo)
            candidate_result_1 = run_pipeline.CandidatePreparationResult("COMMITTED", candidate_1, ("implementation.txt",))
            validation_1 = run_pipeline.ValidationResult("PASS", candidate_1, candidate_1, (), ())
            review_1 = run_pipeline.ReviewResult("PASS", "Changes Requested", candidate_1, 0, "Changes Requested", "", ())
            record["reviewBlock"] = {
                "status": "PASS",
                "decision": "Changes Requested",
                "reasonCode": "REVIEW_SIDE_EFFECT_DIRTY_WORKTREE",
                "reasonCodes": ["REVIEW_SIDE_EFFECT_DIRTY_WORKTREE"],
                "blockCause": "review_side_effect",
                "transient": False,
                "resumeStage": "review",
                "reviewer": "reviewer",
                "candidateSha": candidate_1,
                "validatedSha": candidate_1,
                "baseSha": base_sha,
                "reviewedSha": candidate_1,
                "exitCode": 0,
                "timedOut": False,
            }
            _write = run_pipeline._write_json
            _write(record_path, record)
            (fixture.repo / "implementation.txt").write_text("reviewer side effect 1\n", encoding="utf-8")
            first = pipeline._recover_review_side_effect(config, record_path, record, candidate_result_1, validation_1, review_1, (), "review", [])
            self.assertIsInstance(first, dict)

            (fixture.repo / "implementation.txt").write_text("candidate 2\n", encoding="utf-8")
            self.git(fixture.repo, "add", "implementation.txt")
            self.git(fixture.repo, "commit", "-m", "candidate 2")
            candidate_2 = self.head(fixture.repo)
            candidate_result_2 = run_pipeline.CandidatePreparationResult("COMMITTED", candidate_2, ("implementation.txt",))
            validation_2 = run_pipeline.ValidationResult("PASS", candidate_2, candidate_2, (), ())
            review_2 = run_pipeline.ReviewResult("PASS", "Approved", candidate_2, 0, "Approved", "", ())
            updated_record = json.loads(record_path.read_text(encoding="utf-8"))
            updated_record["reviewBlock"] = {
                "status": "PASS",
                "decision": "Approved",
                "reasonCode": "REVIEW_SIDE_EFFECT_DIRTY_WORKTREE",
                "reasonCodes": ["REVIEW_SIDE_EFFECT_DIRTY_WORKTREE"],
                "blockCause": "review_side_effect",
                "transient": False,
                "resumeStage": "review",
                "reviewer": "reviewer",
                "candidateSha": candidate_2,
                "validatedSha": candidate_2,
                "baseSha": base_sha,
                "reviewedSha": candidate_2,
                "exitCode": 0,
                "timedOut": False,
            }
            _write(record_path, updated_record)
            (fixture.repo / "implementation.txt").write_text("reviewer side effect 2\n", encoding="utf-8")
            second = pipeline._recover_review_side_effect(config, record_path, updated_record, candidate_result_2, validation_2, review_2, (), "review", [])
            self.assertIsInstance(second, dict)
            attempts = json.loads(record_path.read_text(encoding="utf-8")).get("reviewSideEffectRecoveryAttempts", [])

        self.assertEqual(2, len(attempts))
        self.assertEqual(["RESTORED", "RESTORED"], [attempt["status"] for attempt in attempts])
        self.assertEqual([1, 1], [attempt["cycleRound"] for attempt in attempts])
        self.assertNotEqual(attempts[0]["reviewCycleKey"], attempts[1]["reviewCycleKey"])
        self.assertNotEqual(attempts[0]["candidateSha"], attempts[1]["candidateSha"])

    def test_max_exceeded_side_effect_block_reopens_when_attempts_belong_to_older_cycle(self):
        with self.project() as fixture:
            reviewer = fixture.root / "reviewer.py"
            reviewer.write_text("print('Approved')\n", encoding="utf-8")
            fixture.config = self.write_config(
                fixture.root / "project-config.json",
                fixture.repo,
                reviewer=f'"{sys.executable}" "{reviewer}"',
                review_max_side_effect_recovery_rounds=1,
            )
            record_path, record = self.create_durable_run(fixture, "Resume newer side effect", 1, "REVIEW_BLOCKED")
            worktree = Path(record["featureWorktree"])
            spec_dir = worktree / "specs" / "001-resume-newer-side-effect"
            spec_dir.mkdir(parents=True)
            (spec_dir / "spec.md").write_text("# Resume newer side effect\n", encoding="utf-8")
            (worktree / "implementation.txt").write_text("old candidate\n", encoding="utf-8")
            self.git(worktree, "add", "specs", "implementation.txt")
            self.git(worktree, "commit", "-m", "spec 001: old candidate")
            old_candidate_sha = self.head(worktree)
            (worktree / "implementation.txt").write_text("new candidate\n", encoding="utf-8")
            self.git(worktree, "add", "implementation.txt")
            self.git(worktree, "commit", "-m", "spec 001: new candidate")
            candidate_sha = self.head(worktree)
            record["status"] = "REVIEW_BLOCKED"
            record["nextStage"] = "review"
            record["reviewBlock"] = {
                "status": "PASS",
                "decision": "Approved",
                "reasonCode": "REVIEW_SIDE_EFFECT_RECOVERY_MAX_ROUNDS_EXCEEDED",
                "reasonCodes": ["REVIEW_SIDE_EFFECT_RECOVERY_MAX_ROUNDS_EXCEEDED"],
                "blockCause": "review_side_effect",
                "transient": False,
                "resumeStage": "review",
                "reviewer": record["reviewer"],
                "candidateSha": candidate_sha,
                "validatedSha": candidate_sha,
                "baseSha": record["authoritativeBaseSha"],
                "reviewedSha": candidate_sha,
                "exitCode": 0,
                "timedOut": False,
            }
            record["reviewSideEffectRecoveryAttempts"] = [
                {
                    "round": 1,
                    "maxRounds": 1,
                    "status": "RESTORED",
                    "source": "review",
                    "candidateSha": old_candidate_sha,
                    "validatedSha": old_candidate_sha,
                    "reviewedSha": old_candidate_sha,
                    "reviewDecision": "Approved",
                    "dirtyTrackedPaths": ["implementation.txt"],
                    "untrackedPaths": [],
                    "stagedPaths": [],
                    "cleanAfterRecovery": True,
                    "restoreViolations": [],
                }
            ]
            record_path.write_text(json.dumps(record, indent=2, sort_keys=True), encoding="utf-8")
            record_path.with_name("candidate.json").write_text(json.dumps({"status": "COMMITTED", "candidate_sha": candidate_sha, "changed_files": ["specs/001-resume-newer-side-effect/spec.md", "implementation.txt"]}, indent=2, sort_keys=True), encoding="utf-8")
            record_path.with_name("validation-runtime.json").write_text(json.dumps({"status": "PASS", "head_before": candidate_sha, "head_after": candidate_sha, "commands": [], "violations": []}, indent=2, sort_keys=True), encoding="utf-8")
            record_path.with_name("review-runtime.json").write_text(json.dumps({"status": "PASS", "decision": "Approved", "reviewed_sha": candidate_sha, "exit_code": 0, "stdout": "Approved", "stderr": "", "violations": []}, indent=2, sort_keys=True), encoding="utf-8")
            (worktree / "implementation.txt").write_text("reviewer side effect\n", encoding="utf-8")
            status = StatusService().run(StatusRequest(fixture.repo, fixture.config))
            result = RunService(pipeline=RunPipeline(publisher=FakePublisher(fixture.repo))).run(RunRequest(fixture.repo, "Resume newer side effect", 1, fixture.config))
            attempts = result.pipeline_result.run_record.get("reviewSideEffectRecoveryAttempts", [])

        self.assertEqual("True", status.workflow.evidence["resumable"])
        self.assertTrue(result.resumed)
        self.assertEqual("COMPLETE", result.status)
        self.assertEqual(2, len(attempts))
        self.assertEqual("RESTORED", attempts[1]["status"])
        self.assertEqual(1, attempts[1]["cycleRound"])
        self.assertEqual(candidate_sha, attempts[1]["candidateSha"])
        self.assertNotIn("ACTIVE_WORKTREE_PRESENT", self.codes(result))

    def test_cleanup_resume_retries_primary_update_before_complete(self):
        with self.project() as fixture:
            requirements = fixture.root / "requirements.md"
            requirements.write_text("cleanup retry must preserve durable requirements\n", encoding="utf-8")
            blocker = PipelineViolation("PRIMARY_FETCH_FAILED", "primary fetch failed after merge", {"stderr": "blocked"})
            with mock.patch("ados.run_pipeline._update_primary_main", side_effect=[(blocker,), ()]):
                first = RunService(pipeline=RunPipeline(publisher=FakePublisher(fixture.repo))).run(
                    RunRequest(fixture.repo, "Retry primary update", None, fixture.config, requirements_file=requirements)
                )
                primary_run_dir = fixture.repo / ".agent-workflow" / "runs" / "001-retry-primary-update"
                requirements_copy_exists_after_block = (primary_run_dir / "requirements-source.md").exists()
                second = RunService(pipeline=RunPipeline(publisher=FakePublisher(fixture.repo))).run(RunRequest(fixture.repo, "Retry primary update", None, fixture.config))

        self.assertEqual("CLEANUP_INCOMPLETE", first.status)
        self.assertTrue(requirements_copy_exists_after_block)
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

    def write_config(self, path, repo, *, project_id="example-project", allowed_paths=(), implementer=None, implementer_mode="success", reviewer=None, bootstrap_commands=None, validation_commands=None, validation_max_recovery_rounds=None, implementation_max_recovery_rounds=None, implementation_max_recovery_reopens=None, review_max_side_effect_recovery_rounds=None):
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
        if validation_max_recovery_rounds is not None:
            config["execution_policy"]["validation"]["max_recovery_rounds"] = validation_max_recovery_rounds
        implementation = {}
        if implementation_max_recovery_rounds is not None:
            implementation["max_recovery_rounds"] = implementation_max_recovery_rounds
        if implementation_max_recovery_reopens is not None:
            implementation["max_recovery_reopens"] = implementation_max_recovery_reopens
        if implementation:
            config["execution_policy"]["implementation"] = implementation
        if review_max_side_effect_recovery_rounds is not None:
            config["execution_policy"]["review"]["max_side_effect_recovery_rounds"] = review_max_side_effect_recovery_rounds
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

    def create_orphaned_candidate(self, fixture, feature, spec, *, branch=None, path=None, commit=True):
        service = RunService()
        config = load_project_config(fixture.config)
        plan = service._plan(fixture.repo, config, RunRequest(fixture.repo, feature, spec, fixture.config, dry_run=True))
        worktree = Path(path or plan.feature_worktree)
        feature_branch = branch or plan.feature_branch
        self.git(fixture.repo, "worktree", "add", "-b", feature_branch, str(worktree), plan.authoritative_base_sha)
        if commit:
            spec_dir = worktree / "specs" / f"{plan.spec_number}-{plan.feature_slug}"
            spec_dir.mkdir(parents=True)
            (spec_dir / "spec.md").write_text(f"# {feature}\n", encoding="utf-8")
            (worktree / "orphan.txt").write_text("orphaned candidate\n", encoding="utf-8")
            self.git(worktree, "add", "specs", "orphan.txt")
            self.git(worktree, "commit", "-m", f"spec {plan.spec_number}: {feature}")
        return plan, worktree, self.head(worktree)

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

    def create_exhausted_review_side_effect_blocked_run(self, fixture, feature, spec):
        record_path, record = self.create_durable_run(fixture, feature, spec, "REVIEW_BLOCKED")
        worktree = Path(record["featureWorktree"])
        spec_dir = worktree / "specs" / f"{record['specNumber']}-{record['featureSlug']}"
        spec_dir.mkdir(parents=True)
        (spec_dir / "spec.md").write_text(f"# {feature}\n", encoding="utf-8")
        (worktree / "implementation.txt").write_text("old candidate\n", encoding="utf-8")
        self.git(worktree, "add", "specs", "implementation.txt")
        self.git(worktree, "commit", "-m", f"spec {record['specNumber']}: {feature}")
        old_sha = self.head(worktree)
        (worktree / "implementation.txt").write_text("manual side-effect repair\n", encoding="utf-8")
        self.git(worktree, "add", "implementation.txt")
        self.git(worktree, "commit", "-m", "manual review side effect repair")
        new_sha = self.head(worktree)
        record["status"] = "REVIEW_BLOCKED"
        record["nextStage"] = "recovery"
        record["reviewBlock"] = {
            "status": "PASS",
            "decision": "Approved",
            "reasonCode": "REVIEW_SIDE_EFFECT_RECOVERY_MAX_ROUNDS_EXCEEDED",
            "reasonCodes": ["REVIEW_SIDE_EFFECT_RECOVERY_MAX_ROUNDS_EXCEEDED"],
            "blockCause": "review_side_effect",
            "transient": False,
            "resumeStage": "",
            "reviewer": record["reviewer"],
            "candidateSha": old_sha,
            "validatedSha": old_sha,
            "baseSha": record["authoritativeBaseSha"],
            "reviewedSha": old_sha,
            "exitCode": 0,
            "timedOut": False,
            "evidence": {"max_rounds": "1", "candidate_sha": old_sha, "review_cycle_key": f"{old_sha}|{old_sha}|{old_sha}|Approved"},
        }
        record["reviewSideEffectRecoveryAttempts"] = [
            {
                "round": 1,
                "cycleRound": 1,
                "reviewCycleKey": f"{old_sha}|{old_sha}|{old_sha}|Approved",
                "maxRounds": 1,
                "status": "RESTORED",
                "source": "review",
                "candidateSha": old_sha,
                "validatedSha": old_sha,
                "reviewedSha": old_sha,
                "reviewDecision": "Approved",
                "dirtyTrackedPaths": ["implementation.txt"],
                "untrackedPaths": [],
                "stagedPaths": [],
                "cleanAfterRecovery": True,
                "restoreViolations": [],
            }
        ]
        record_path.write_text(json.dumps(record, indent=2, sort_keys=True), encoding="utf-8")
        record_path.with_name("candidate.json").write_text(
            json.dumps({"status": "COMMITTED", "candidate_sha": old_sha, "changed_files": [f"specs/{record['specNumber']}-{record['featureSlug']}/spec.md", "implementation.txt"]}, indent=2, sort_keys=True),
            encoding="utf-8",
        )
        record_path.with_name("validation-runtime.json").write_text(
            json.dumps({"status": "PASS", "head_before": old_sha, "head_after": old_sha, "commands": [], "violations": []}, indent=2, sort_keys=True),
            encoding="utf-8",
        )
        record_path.with_name("review-runtime.json").write_text(
            json.dumps({"status": "PASS", "decision": "Approved", "reviewed_sha": old_sha, "exit_code": 0, "stdout": "Approved", "stderr": "", "violations": []}, indent=2, sort_keys=True),
            encoding="utf-8",
        )
        return record_path, record, old_sha, new_sha

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

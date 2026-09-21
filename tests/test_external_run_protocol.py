import hashlib
import contextlib
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from ados.convergence_evidence import evaluate_convergence
from ados.cli import main
from ados.external_origin import ExternalOriginViolation, parse_external_origin
from ados.external_run import ExternalRunProtocolService


class ExternalRunProtocolTests(unittest.TestCase):
    def test_origin_contract_digest_and_safety(self):
        raw = self.origin("a" * 64)
        parsed = parse_external_origin(raw)
        self.assertFalse(isinstance(parsed, ExternalOriginViolation))
        self.assertEqual(parsed.digest, parse_external_origin(dict(reversed(list(raw.items())))).digest)

        cases = [
            ({**raw, "purpose": "spec-verification"}, "EXTERNAL_ORIGIN_PURPOSE_INVALID"),
            ({**raw, "recursionDepth": 1}, "EXTERNAL_ORIGIN_RECURSION_BLOCKED"),
            ({**raw, "runId": "caller-picked"}, "EXTERNAL_ORIGIN_RUN_ID_FORBIDDEN"),
        ]
        for value, code in cases:
            with self.subTest(code=code):
                result = parse_external_origin(value)
                self.assertIsInstance(result, ExternalOriginViolation)
                self.assertEqual(code, result.code)

    def test_prepare_is_idempotent_bound_and_dispatch_free(self):
        with Fixture() as fixture:
            service = ExternalRunProtocolService()
            first = service.prepare(**fixture.prepare_args())
            second = service.prepare(**fixture.prepare_args())
            conflict_origin = fixture.origin()
            conflict_origin["executionId"] = "execution-other"
            conflict_origin["idempotencyKey"] = "idempotency-other"
            conflict = service.prepare(**fixture.prepare_args(origin=conflict_origin))

            self.assertEqual("PREPARED", first.status)
            self.assertEqual(first.run["runId"], second.run["runId"])
            self.assertTrue(second.run["idempotentResolution"])
            self.assertFalse(fixture.dispatch_marker.exists())
            self.assertEqual(1, len(fixture.run_records()))
            self.assertEqual("BLOCKED", conflict.status)
            self.assertIn("EXTERNAL_ORIGIN_BINDING_MISMATCH", {v.code for v in conflict.violations})
            record_path = fixture.record_path(first.run)
            self.assertTrue(record_path.with_name("external-origin.json").is_file())

    def test_prepare_rejects_requirements_mismatch_and_recursion(self):
        with Fixture() as fixture:
            bad = fixture.origin()
            bad["requirementsSha256"] = "0" * 64
            mismatch = ExternalRunProtocolService().prepare(**fixture.prepare_args(origin=bad))
            recursive = fixture.origin()
            recursive["recursionDepth"] = 1
            blocked = ExternalRunProtocolService().prepare(**fixture.prepare_args(origin=recursive))
            self.assertEqual("INVALID", mismatch.status)
            self.assertEqual("INVALID", blocked.status)
            self.assertEqual("EXTERNAL_ORIGIN_RECURSION_BLOCKED", blocked.violations[0].code)
            self.assertEqual([], fixture.run_records())

    def test_idempotency_identity_cannot_be_reused_for_another_feature(self):
        with Fixture() as fixture:
            service = ExternalRunProtocolService()
            first = service.prepare(**fixture.prepare_args())
            conflicting = fixture.origin()
            conflicting["requestedFeature"] = "Different Child Feature"
            result = service.prepare(
                project_path=fixture.repo, feature="Different Child Feature", origin=conflicting,
                requirements_file=fixture.requirements, spec_number=2, config_path=fixture.config,
            )
            self.assertEqual("PREPARED", first.status)
            self.assertEqual("BLOCKED", result.status)
            self.assertEqual("EXTERNAL_ORIGIN_IDEMPOTENCY_CONFLICT", result.violations[0].code)
            self.assertEqual(1, len(fixture.run_records()))

    def test_interrupted_origin_artifact_write_is_idempotently_repaired_without_dispatch(self):
        with Fixture() as fixture:
            service = ExternalRunProtocolService()
            first = service.prepare(**fixture.prepare_args())
            origin_artifact = fixture.record_path(first.run).with_name("external-origin.json")
            origin_artifact.unlink()
            second = service.prepare(**fixture.prepare_args())
            self.assertEqual("PREPARED", second.status)
            self.assertTrue(second.run["idempotentResolution"])
            self.assertTrue(origin_artifact.is_file())
            self.assertFalse(fixture.dispatch_marker.exists())

    def test_controlled_two_phase_pipeline_and_read_only_inspection(self):
        with Fixture() as fixture:
            service = ExternalRunProtocolService()
            prepared = service.prepare(**fixture.prepare_args())
            run_id = prepared.run["runId"]
            digest = prepared.run["originDigest"]
            before_records = len(fixture.run_records())
            continued = service.continue_exact(project_path=fixture.repo, run_id=run_id, origin_digest=digest, config_path=fixture.config)
            record_path = fixture.record_path(prepared.run)
            before = fixture.artifact_snapshot(record_path.parent)
            inspected = service.inspect(project_path=fixture.repo, run_id=run_id, origin_digest=digest, config_path=fixture.config)
            after = fixture.artifact_snapshot(record_path.parent)

            self.assertEqual("READY_FOR_PUBLICATION", continued.status)
            self.assertEqual(before_records, len(fixture.run_records()))
            self.assertEqual("PASS", inspected.status)
            self.assertEqual(run_id, inspected.run["runId"])
            self.assertEqual("PASS", inspected.run["validation"]["status"])
            self.assertEqual("Approved", inspected.run["review"]["decision"])
            self.assertEqual("MATCH", inspected.run["exactHead"])
            self.assertEqual("claude", inspected.run["assignment"]["implementerId"])
            self.assertEqual("claude", inspected.run["assignment"]["candidateOwnerId"])
            self.assertEqual("codex", inspected.run["assignment"]["reviewerId"])
            self.assertEqual("READY", inspected.run["technicalPublicationReadiness"])
            self.assertEqual("NOT_REQUESTED", inspected.run["remotePublicationState"])
            self.assertEqual("HUMAN_REQUIRED", inspected.run["remotePublicationAuthority"])
            self.assertEqual("MATCH", inspected.run["convergenceArtifactIntegrity"])
            self.assertEqual(before, after)
            self.assertEqual("1", fixture.dispatch_marker.read_text(encoding="utf-8"))
            self.assertFalse(fixture.bare_branch_exists(prepared.run["branch"]))

    def test_exact_continue_rejects_wrong_identity_without_dispatch(self):
        with Fixture() as fixture:
            service = ExternalRunProtocolService()
            prepared = service.prepare(**fixture.prepare_args())
            wrong_run = service.continue_exact(project_path=fixture.repo, run_id="missing", origin_digest=prepared.run["originDigest"], config_path=fixture.config)
            wrong_origin = service.continue_exact(project_path=fixture.repo, run_id=prepared.run["runId"], origin_digest="0" * 64, config_path=fixture.config)
            self.assertEqual("BLOCKED", wrong_run.status)
            self.assertEqual("EXACT_RUN_NOT_FOUND", wrong_run.violations[0].code)
            self.assertEqual("BLOCKED", wrong_origin.status)
            self.assertEqual("EXTERNAL_ORIGIN_DIGEST_MISMATCH", wrong_origin.violations[0].code)
            self.assertFalse(fixture.dispatch_marker.exists())

    def test_cli_prepare_emits_stable_json(self):
        with Fixture() as fixture:
            origin_file = fixture.root / "origin.json"
            origin_file.write_text(json.dumps(fixture.origin()), encoding="utf-8")
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                code = main([
                    "external-run", "prepare", "--project", str(fixture.repo), "--config", str(fixture.config),
                    "--feature", "Child Protocol Exercise", "--spec", "1", "--requirements-file", str(fixture.requirements),
                    "--origin-file", str(origin_file), "--json",
                ])
            payload = json.loads(output.getvalue())
            self.assertEqual(0, code)
            self.assertEqual(1, payload["schemaVersion"])
            self.assertEqual("prepare", payload["operation"])
            self.assertEqual("PREPARED", payload["status"])
            self.assertTrue(payload["run"]["runId"])
            self.assertFalse(fixture.dispatch_marker.exists())

    def test_inspection_detects_stale_head_and_artifact_mismatch(self):
        with Fixture() as fixture:
            service = ExternalRunProtocolService()
            prepared = service.prepare(**fixture.prepare_args())
            service.continue_exact(project_path=fixture.repo, run_id=prepared.run["runId"], origin_digest=prepared.run["originDigest"], config_path=fixture.config)
            worktree = Path(prepared.run["worktree"])
            (worktree / "later.txt").write_text("later\n", encoding="utf-8")
            fixture.git(worktree, "add", "later.txt")
            fixture.git(worktree, "commit", "-m", "later drift")
            inspected = service.inspect(project_path=fixture.repo, run_id=prepared.run["runId"], origin_digest=prepared.run["originDigest"], config_path=fixture.config)
            self.assertEqual("MISMATCH", inspected.run["exactHead"])
            self.assertEqual("BLOCKED", inspected.run["technicalPublicationReadiness"])
            self.assertEqual("MISMATCH", inspected.run["convergenceArtifactIntegrity"])

    def test_convergence_failure_matrix(self):
        with Fixture() as fixture:
            service = ExternalRunProtocolService()
            prepared = service.prepare(**fixture.prepare_args())
            service.continue_exact(project_path=fixture.repo, run_id=prepared.run["runId"], origin_digest=prepared.run["originDigest"], config_path=fixture.config)
            record_path = fixture.record_path(prepared.run)
            original_record = json.loads(record_path.read_text(encoding="utf-8"))
            original_candidate = json.loads(record_path.with_name("candidate.json").read_text(encoding="utf-8"))
            original_validation = json.loads(record_path.with_name("validation-runtime.json").read_text(encoding="utf-8"))
            original_review = json.loads(record_path.with_name("review-runtime.json").read_text(encoding="utf-8"))

            mutations = {
                "validation failure": ("validation-runtime.json", {**original_validation, "status": "BLOCK"}, "VALIDATION_NOT_PASS"),
                "changes requested": ("review-runtime.json", {**original_review, "decision": "Changes Requested"}, "REVIEW_NOT_APPROVED"),
                "review unavailable": ("review-runtime.json", {**original_review, "status": "BLOCK", "decision": "Unavailable"}, "REVIEW_RUNTIME_NOT_PASS"),
                "candidate validation mismatch": ("validation-runtime.json", {**original_validation, "head_after": "f" * 40}, "CANDIDATE_VALIDATION_SHA_MISMATCH"),
                "candidate reviewed mismatch": ("review-runtime.json", {**original_review, "reviewed_sha": "e" * 40}, "CANDIDATE_REVIEW_SHA_MISMATCH"),
            }
            for label, (name, payload, code) in mutations.items():
                with self.subTest(label=label):
                    record_path.with_name("candidate.json").write_text(json.dumps(original_candidate), encoding="utf-8")
                    record_path.with_name("validation-runtime.json").write_text(json.dumps(original_validation), encoding="utf-8")
                    record_path.with_name("review-runtime.json").write_text(json.dumps(original_review), encoding="utf-8")
                    record_path.with_name(name).write_text(json.dumps(payload), encoding="utf-8")
                    evidence = evaluate_convergence(run_record_path=record_path, record=original_record)
                    self.assertEqual("BLOCKED", evidence["technicalPublicationReadiness"])
                    self.assertIn(code, {item["code"] for item in evidence["blockingReasons"]})

            same_roles = {**original_record, "agentAssignment": {"implementerId": "same", "reviewerId": "same", "candidateOwnerId": "same"}}
            record_path.with_name("validation-runtime.json").write_text(json.dumps(original_validation), encoding="utf-8")
            record_path.with_name("review-runtime.json").write_text(json.dumps(original_review), encoding="utf-8")
            evidence = evaluate_convergence(run_record_path=record_path, record=same_roles)
            codes = {item["code"] for item in evidence["blockingReasons"]}
            self.assertIn("REVIEWER_NOT_INDEPENDENT_FROM_IMPLEMENTER", codes)
            self.assertIn("REVIEWER_NOT_INDEPENDENT_FROM_CANDIDATE_OWNER", codes)

    @staticmethod
    def origin(requirements_sha):
        return {
            "originSystem": "project-a", "originSchemaVersion": 1, "projectId": "project-a",
            "backlogTaskId": "task-1", "developmentRequestId": "request-1",
            "preparationId": "preparation-1", "executionId": "execution-1",
            "requirementsSha256": requirements_sha, "requestedFeature": "Child Protocol Exercise",
            "purpose": "external-project-development", "recursionDepth": 0,
            "idempotencyKey": "project-a/execution-1", "createdAt": "2026-09-20T12:00:00Z",
        }


class Fixture:
    def __init__(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.repo = self.root / "primary"
        self.bare = self.root / "origin.git"
        self.config = self.root / "project-config.json"
        self.requirements = self.root / "requirements.md"
        self.dispatch_marker = self.root / "dispatch-count.txt"

    def __enter__(self):
        self.git(self.root, "init", "--bare", str(self.bare))
        self.git(self.root, "init", "-b", "main", str(self.repo))
        self.git(self.repo, "config", "user.email", "test@example.invalid")
        self.git(self.repo, "config", "user.name", "Test User")
        (self.repo / ".gitignore").write_text(".agent-workflow/\n", encoding="utf-8")
        (self.repo / "README.md").write_text("base\n", encoding="utf-8")
        self.git(self.repo, "add", ".gitignore", "README.md")
        self.git(self.repo, "commit", "-m", "initial")
        self.git(self.repo, "remote", "add", "origin", str(self.bare))
        self.git(self.repo, "push", "-u", "origin", "main")
        self.requirements.write_text("implement a bounded child protocol exercise\n", encoding="utf-8")
        implementer = self.root / "implementer.py"
        implementer.write_text(
            "from pathlib import Path\n"
            f"p=Path(r'{self.dispatch_marker}')\n"
            "p.write_text(str(int(p.read_text())+1) if p.exists() else '1', encoding='utf-8')\n"
            "Path('implementation.txt').write_text('child implementation\\n', encoding='utf-8')\n",
            encoding="utf-8",
        )
        reviewer = self.root / "reviewer.py"
        reviewer.write_text("print('Approved')\n", encoding="utf-8")
        command_i = f'"{sys.executable}" "{implementer}"'
        command_r = f'"{sys.executable}" "{reviewer}"'
        config = {
            "project": {"id": "child-fixture", "primary_repository_path": str(self.repo), "default_branch": "main", "allowed_primary_local_paths": []},
            "roles": {"implementer": command_i, "reviewer": command_r},
            "bootstrap": {"commands": []},
            "execution_policy": {
                "schema_version": "1", "publication": {"merge_strategy": "merge"},
                "review": {"reviewer": command_r, "max_rounds": 1}, "cleanup": {"autonomous": True},
                "guardian": {"stop_on_uncertain": True}, "validation": {"commands": ["git diff --check"]},
                "agent_roles": {
                    "mode": "adaptive", "agents": {"claude": command_i, "codex": command_r},
                    "implementer_preference": ["claude", "codex"], "reviewer_preference": ["codex", "claude"],
                },
            },
        }
        self.config.write_text(json.dumps(config), encoding="utf-8")
        return self

    def __exit__(self, *_):
        subprocess.run(("git", "worktree", "prune"), cwd=self.repo, capture_output=True)
        self.temp.cleanup()

    def origin(self):
        content = self.requirements.read_text(encoding="utf-8")
        canonical = content.replace("\r\n", "\n").replace("\r", "\n")
        if not canonical.endswith("\n"):
            canonical += "\n"
        return ExternalRunProtocolTests.origin(hashlib.sha256(canonical.encode()).hexdigest())

    def prepare_args(self, origin=None):
        return {"project_path": self.repo, "feature": "Child Protocol Exercise", "origin": origin or self.origin(), "requirements_file": self.requirements, "spec_number": 1, "config_path": self.config}

    def record_path(self, run):
        return Path(run["worktree"]) / ".agent-workflow" / "runs" / "001-child-protocol-exercise" / "ados-run.json"

    def run_records(self):
        return list(self.root.glob("**/.agent-workflow/runs/*/ados-run.json"))

    def artifact_snapshot(self, directory):
        return {path.name: path.read_bytes() for path in directory.iterdir() if path.is_file()}

    def bare_branch_exists(self, branch):
        result = subprocess.run(("git", "show-ref", "--verify", f"refs/heads/{branch}"), cwd=self.bare, capture_output=True)
        return result.returncode == 0

    @staticmethod
    def git(cwd, *args):
        return subprocess.run(("git", *args), cwd=cwd, check=True, capture_output=True, text=True)


if __name__ == "__main__":
    unittest.main()

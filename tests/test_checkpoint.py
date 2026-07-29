from __future__ import annotations

import tempfile
import unittest
from collections import OrderedDict
from pathlib import Path

from project_glacier_orchestrator.canonical import sha256_hex
from project_glacier_orchestrator.checkpoint import (
    ResumeDocument,
    ResumeStore,
    WorkspacePaths,
    secure_read_bytes,
)
from project_glacier_orchestrator.errors import OrchestratorError
from project_glacier_orchestrator.workflow import (
    MUTABLE_COORDINATION_FIELDS,
    REQUIRED_COORDINATION_KEYS,
    validate_coordination_state,
)
from tests.support import make_workspace

FINAL_WORKER_PROMPT_DIGEST = "d3eab62d05ee5991710bd125961fb34f5198dd1aa71b9bb8b6967daa9ab4da4c"


class CheckpointTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = make_workspace(Path(self.temporary.name))
        self.paths = WorkspacePaths.from_root(self.root)
        self.store = ResumeStore(
            self.paths,
            required_keys=REQUIRED_COORDINATION_KEYS,
            state_validator=validate_coordination_state,
        )

    def assert_error(self, code: str, function, *args, **kwargs) -> None:
        with self.assertRaises(OrchestratorError) as caught:
            function(*args, **kwargs)
        self.assertEqual(code, caught.exception.code)

    def test_generated_fixture_has_valid_hash_and_artifact_digests(self) -> None:
        document = self.store.read()
        document.validate(required_keys=REQUIRED_COORDINATION_KEYS)
        self.assertEqual(document.state_hash, document.computed_hash())
        digests = self.store.artifact_digests()
        self.assertEqual(document.values["rules_digest"], digests.rules)
        self.assertEqual(document.values["ledger_digest"], digests.ledger)
        self.assertEqual(document.values["offline_worker_prompt_digest"], digests.worker_prompt)
        self.assertEqual(FINAL_WORKER_PROMPT_DIGEST, digests.worker_prompt)

    def test_checkpoint_hash_detects_any_unreconciled_value_change(self) -> None:
        text = self.paths.resume.read_text(encoding="utf-8")
        self.paths.resume.write_text(
            text.replace("worker_coordination_blocker: none", "worker_coordination_blocker: changed"),
            encoding="utf-8",
        )
        self.assert_error("CHECKPOINT_HASH_MISMATCH", self.store.read)

    def test_dry_run_performs_cas_but_creates_no_lock_and_writes_nothing(self) -> None:
        current = self.store.read()
        original = self.paths.resume.read_bytes()
        planned = self.store.update(
            expected_version=current.version,
            expected_hash=current.state_hash,
            updates={"worker_coordination_blocker": "dry-run"},
            allowed_fields=MUTABLE_COORDINATION_FIELDS,
            commit=False,
        )
        self.assertEqual(current.version + 1, planned.version)
        self.assertEqual("dry-run", planned.values["worker_coordination_blocker"])
        self.assertEqual(original, self.paths.resume.read_bytes())
        self.assertFalse(self.paths.lock.exists())

    def test_stale_version_or_hash_fails_compare_and_swap(self) -> None:
        current = self.store.read()
        for version, state_hash in (
            (current.version + 1, current.state_hash),
            (current.version, "f" * 64),
        ):
            with self.subTest(version=version, state_hash=state_hash):
                self.assert_error(
                    "CHECKPOINT_CAS_CONFLICT",
                    self.store.update,
                    expected_version=version,
                    expected_hash=state_hash,
                    updates={"worker_coordination_blocker": "cas"},
                    allowed_fields=MUTABLE_COORDINATION_FIELDS,
                    commit=False,
                )

    def test_commit_updates_version_hash_and_uses_lock_without_leaving_temp_files(self) -> None:
        current = self.store.read()
        committed = self.store.update(
            expected_version=current.version,
            expected_hash=current.state_hash,
            updates={"worker_coordination_blocker": "committed"},
            allowed_fields=MUTABLE_COORDINATION_FIELDS,
            commit=True,
        )
        self.assertEqual(current.version + 1, committed.version)
        self.assertEqual("committed", self.store.read().values["worker_coordination_blocker"])
        self.assertTrue(self.paths.lock.is_file())
        self.assertEqual([], list(self.root.glob(".PROJECT_GLACIER_RESUME.*")))

    def test_rules_or_ledger_drift_is_blocked_unless_explicitly_reconciled(self) -> None:
        current = self.store.read()
        ledger = self.paths.ledger
        ledger.write_bytes(ledger.read_bytes() + b"\nreviewed test-only change\n")
        self.assert_error(
            "CANONICAL_ARTIFACT_DRIFT",
            self.store.update,
            expected_version=current.version,
            expected_hash=current.state_hash,
            updates={"worker_coordination_blocker": "drift"},
            allowed_fields=MUTABLE_COORDINATION_FIELDS,
            commit=False,
        )
        planned = self.store.update(
            expected_version=current.version,
            expected_hash=current.state_hash,
            updates={"worker_coordination_blocker": "reviewed"},
            allowed_fields=MUTABLE_COORDINATION_FIELDS,
            commit=False,
            allow_rules_ledger_reconciliation=True,
        )
        self.assertEqual(sha256_hex(ledger.read_bytes()), planned.values["ledger_digest"])

    def test_worker_prompt_drift_has_a_separate_fail_closed_error(self) -> None:
        current = self.store.read()
        prompt = self.paths.worker_prompt
        prompt.write_bytes(prompt.read_bytes() + b"\n")
        self.assert_error(
            "WORKER_PROMPT_DRIFT",
            self.store.update,
            expected_version=current.version,
            expected_hash=current.state_hash,
            updates={"worker_coordination_blocker": "drift"},
            allowed_fields=MUTABLE_COORDINATION_FIELDS,
            commit=False,
        )

    def test_symlinked_artifact_is_never_followed(self) -> None:
        target = self.root / "outside.txt"
        target.write_text("outside", encoding="utf-8")
        self.paths.rules.unlink()
        self.paths.rules.symlink_to(target)
        self.assert_error("ARTIFACT_OPEN_FAILED", secure_read_bytes, self.paths.rules)

    def test_symlinked_lock_is_never_followed(self) -> None:
        current = self.store.read()
        target = self.root / "lock-target"
        target.write_text("untouched", encoding="utf-8")
        self.paths.lock.symlink_to(target)
        self.assert_error(
            "LOCK_OPEN_FAILED",
            self.store.update,
            expected_version=current.version,
            expected_hash=current.state_hash,
            updates={"worker_coordination_blocker": "blocked"},
            allowed_fields=MUTABLE_COORDINATION_FIELDS,
            commit=True,
        )
        self.assertEqual("untouched", target.read_text(encoding="utf-8"))

    def test_value_separator_is_first_colon_only_and_round_trips(self) -> None:
        parsed = ResumeDocument.parse(
            "PROJECT_GLACIER_RESUME\n"
            "canonical_state_version: 1\n"
            "canonical_state_hash: " + "0" * 64 + "\n"
            "checkpoint_generated_at: 2026-07-12T00:00:00Z\n"
            "worker_coordination_blocker: reason:detail:more\n"
        )
        self.assertEqual("reason:detail:more", parsed.values["worker_coordination_blocker"])
        reparsed = ResumeDocument.parse(parsed.render())
        self.assertEqual(parsed.values, reparsed.values)

    def test_checkpoint_values_reject_newlines_and_control_characters(self) -> None:
        document = ResumeDocument(OrderedDict([("value", "line one\nline two")]))
        self.assert_error("CHECKPOINT_VALUE_INVALID", document.render)
        document = ResumeDocument(OrderedDict([("value", "bad\x00value")]))
        self.assert_error("CHECKPOINT_VALUE_INVALID", document.render)

    def test_json_replay_arrays_are_unique_and_bounded(self) -> None:
        document = self.store.read()
        document.values["multi_root_processed_message_ids"] = '["same","same"]'
        self.assert_error(
            "CHECKPOINT_ARRAY_INVALID",
            document.json_array,
            "multi_root_processed_message_ids",
            maximum=256,
        )


if __name__ == "__main__":
    unittest.main()

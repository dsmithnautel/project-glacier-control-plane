from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from project_glacier_orchestrator.errors import OrchestratorError
from project_glacier_orchestrator.outbox import OutboxStore


class OutboxTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.store = OutboxStore(self.root)

    def assert_error(self, code: str, function, *args, **kwargs) -> None:
        with self.assertRaises(OrchestratorError) as caught:
            function(*args, **kwargs)
        self.assertEqual(code, caught.exception.code)

    def test_put_is_idempotent_only_for_identical_content(self) -> None:
        record = self.store.build_record(
            message_id="message-1",
            message_type="ACK",
            payload={"type": "ACK", "value": "same"},
        )
        self.store.put(record)
        first_bytes = (self.store.directory / "message-1.json").read_bytes()
        self.store.put(record)
        self.assertEqual(first_bytes, (self.store.directory / "message-1.json").read_bytes())
        self.assertEqual(record, self.store.load("message-1"))

        collision = self.store.build_record(
            message_id="message-1",
            message_type="ACK",
            payload={"type": "ACK", "value": "different"},
        )
        self.assert_error("OUTBOX_ID_COLLISION", self.store.put, collision)

    def test_payload_tampering_is_detected(self) -> None:
        record = self.store.build_record(
            message_id="message-2",
            message_type="STOP_ALL",
            payload={"type": "STOP_ALL", "reason_code": "CLEANUP"},
        )
        self.store.put(record)
        path = self.store.directory / "message-2.json"
        document = json.loads(path.read_text(encoding="utf-8"))
        document["payload"]["reason_code"] = "PILOT_ABORT"
        path.write_text(json.dumps(document, separators=(",", ":")), encoding="utf-8")
        self.assert_error("OUTBOX_DIGEST_MISMATCH", self.store.load, "message-2")

    def test_record_schema_is_closed_and_identity_is_bound_to_filename(self) -> None:
        record = self.store.build_record(
            message_id="message-3",
            message_type="ACK",
            payload="sanitized text",
        )
        self.store.put(record)
        path = self.store.directory / "message-3.json"
        document = json.loads(path.read_text(encoding="utf-8"))
        document["extra"] = True
        path.write_text(json.dumps(document, separators=(",", ":")), encoding="utf-8")
        self.assert_error("OUTBOX_SCHEMA_INVALID", self.store.load, "message-3")

    def test_identifiers_cannot_escape_the_outbox_directory(self) -> None:
        self.assert_error(
            "OUTBOX_MESSAGE_ID_INVALID",
            self.store.build_record,
            message_id="../escape",
            message_type="ACK",
            payload={},
        )
        self.assert_error("OUTBOX_MESSAGE_ID_INVALID", self.store.load, "../escape")

    def test_symlinked_outbox_directory_is_rejected(self) -> None:
        outside = self.root / "outside"
        outside.mkdir()
        self.store.state_directory.mkdir()
        self.store.directory.symlink_to(outside, target_is_directory=True)
        record = self.store.build_record(
            message_id="message-4",
            message_type="ACK",
            payload={},
        )
        self.assert_error("OUTBOX_DIRECTORY_UNSAFE", self.store.put, record)
        self.assertEqual([], list(outside.iterdir()))


if __name__ == "__main__":
    unittest.main()

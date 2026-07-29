from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from project_glacier_orchestrator.canonical import canonical_json_bytes
from project_glacier_orchestrator.errors import OrchestratorError
from project_glacier_orchestrator.outbox import payload_digest
from project_glacier_orchestrator.protocol import parse_packet, validate_ack
from project_glacier_orchestrator.workflow import MUTABLE_COORDINATION_FIELDS, Coordinator
from tests.support import (
    DeterministicIds,
    make_workspace,
    packet_text,
    valid_assignment_spec,
    valid_attestation,
    valid_result_packet,
)


class CoordinatorLifecycleTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = make_workspace(Path(self.temporary.name))
        self.coordinator = Coordinator(self.root, id_factory=DeterministicIds())

    def assert_error(self, code: str, function, *args, **kwargs) -> None:
        with self.assertRaises(OrchestratorError) as caught:
            function(*args, **kwargs)
        self.assertEqual(code, caught.exception.code)

    @staticmethod
    def fence(document) -> dict[str, object]:
        return {
            "expected_version": document.version,
            "expected_hash": document.state_hash,
            "commit": True,
        }

    def activate_pilot(self):
        current = self.coordinator.store.read()
        return self.coordinator.activate_pilot(
            **self.fence(current),
            user_authorized_offline_worker=True,
        )

    def create_attested_worker(self):
        activated = self.activate_pilot()
        bootstrap = self.coordinator.plan_bootstrap(**self.fence(activated.checkpoint))
        created = self.coordinator.record_worker_created(
            **self.fence(bootstrap.checkpoint),
            thread_id="worker-task-1",
            host_id="none",
            ambiguity_resolved=False,
        )
        return self.coordinator.accept_attestation(
            **self.fence(created.checkpoint),
            source_thread_id="worker-task-1",
            packet_text=packet_text(
                valid_attestation(
                    bootstrap_id=created.checkpoint.values["offline_worker_bootstrap_message_id"],
                    prompt_digest=created.checkpoint.values["offline_worker_prompt_digest"],
                )
            ),
            task_inactive=True,
        )

    def create_planned_ack(self):
        attested = self.create_attested_worker()
        assignment_planned = self.coordinator.plan_assignment(
            **self.fence(attested.checkpoint),
            spec=valid_assignment_spec(),
        )
        assignment = parse_packet(assignment_planned.action.as_dict()["arguments"]["prompt"])
        assigned = self.coordinator.record_delivery(
            **self.fence(assignment_planned.checkpoint),
            message_id=assignment["message_id"],
            delivery_status="ACKNOWLEDGED",
        )
        result = self.coordinator.accept_result(
            **self.fence(assigned.checkpoint),
            source_thread_id="worker-task-1",
            packet_text=packet_text(valid_result_packet(assignment)),
            task_inactive=True,
        )
        disposition = self.coordinator.commit_result(
            **self.fence(result.checkpoint),
            disposition="ACCEPTED",
            artifacts_reviewed=False,
        )
        return self.coordinator.plan_ack(**self.fence(disposition.checkpoint))

    def test_complete_offline_lifecycle_emits_actions_but_calls_no_external_system(self) -> None:
        initial = self.coordinator.store.read()
        doctor = self.coordinator.doctor()
        self.assertTrue(doctor["ok"])
        self.assertFalse(doctor["pilot_activated"])
        self.assertFalse(doctor["network_or_glacier_calls_performed"])

        self.assert_error(
            "EXPLICIT_ACTIVATION_REQUIRED",
            self.coordinator.activate_pilot,
            **self.fence(initial),
            user_authorized_offline_worker=False,
        )
        activated = self.coordinator.activate_pilot(
            **self.fence(initial),
            user_authorized_offline_worker=True,
        )
        self.assertEqual("MULTI_ROOT_PILOT", activated.checkpoint.values["orchestration_mode"])

        bootstrap = self.coordinator.plan_bootstrap(**self.fence(activated.checkpoint))
        self.assertTrue(bootstrap.committed)
        self.assertEqual("CREATING", bootstrap.checkpoint.values["offline_worker_status"])
        self.assertEqual("PLANNED", bootstrap.checkpoint.values["coordination_outbox_delivery_status"])
        create_action = bootstrap.action.as_dict()
        self.assertEqual("codex_app__create_thread", create_action["tool"])
        self.assertEqual({"type": "projectless"}, create_action["arguments"]["target"])
        self.assertNotIn("projectId", create_action["arguments"])
        self.assertNotIn("environment", create_action["arguments"])
        bootstrap_record = self.coordinator.outbox.load(
            bootstrap.checkpoint.values["coordination_outbox_message_id"]
        )
        self.assertEqual("CREATE_THREAD", bootstrap_record.message_type)
        self.assertEqual(
            bootstrap.checkpoint.values["coordination_outbox_payload_digest"],
            bootstrap_record.payload_digest,
        )
        self.assertEqual(create_action, self.coordinator.pending_action()["action"])

        created = self.coordinator.record_worker_created(
            **self.fence(bootstrap.checkpoint),
            thread_id="worker-task-1",
            host_id="none",
            ambiguity_resolved=False,
        )
        self.assertEqual("ATTESTING", created.checkpoint.values["offline_worker_status"])
        self.assertEqual("codex_app__read_thread", created.action["tool"])

        attestation_packet = valid_attestation(
            bootstrap_id=created.checkpoint.values["offline_worker_bootstrap_message_id"],
            prompt_digest=created.checkpoint.values["offline_worker_prompt_digest"],
        )
        attested = self.coordinator.accept_attestation(
            **self.fence(created.checkpoint),
            source_thread_id="worker-task-1",
            packet_text=packet_text(attestation_packet),
            task_inactive=True,
        )
        self.assertEqual("IDLE", attested.checkpoint.values["offline_worker_status"])
        self.assertEqual("NONE", attested.checkpoint.values["coordination_outbox_delivery_status"])
        self.assertIn(
            attestation_packet["message_id"],
            attested.checkpoint.json_array("multi_root_processed_message_ids", maximum=256),
        )

        assignment_planned = self.coordinator.plan_assignment(
            **self.fence(attested.checkpoint),
            spec=valid_assignment_spec(),
        )
        self.assertEqual(
            "ASSIGNMENT_PLANNED",
            assignment_planned.checkpoint.values["offline_worker_status"],
        )
        assignment_action = assignment_planned.action.as_dict()
        self.assertEqual("codex_app__send_message_to_thread", assignment_action["tool"])
        self.assertEqual("worker-task-1", assignment_action["arguments"]["threadId"])
        assignment = parse_packet(assignment_action["arguments"]["prompt"])
        self.assertEqual(
            attested.checkpoint.version,
            assignment["base_state_version"],
        )
        self.assertEqual(attested.checkpoint.state_hash, assignment["base_state_hash"])

        assigned = self.coordinator.record_delivery(
            **self.fence(assignment_planned.checkpoint),
            message_id=assignment["message_id"],
            delivery_status="ACKNOWLEDGED",
        )
        self.assertEqual("ASSIGNED", assigned.checkpoint.values["offline_worker_status"])
        self.assertEqual("codex_app__read_thread", assigned.action["tool"])

        result_packet = valid_result_packet(assignment)
        result = self.coordinator.accept_result(
            **self.fence(assigned.checkpoint),
            source_thread_id="worker-task-1",
            packet_text=packet_text(result_packet),
            task_inactive=True,
        )
        self.assertEqual("RESULT_VALIDATED", result.checkpoint.values["offline_worker_status"])
        self.assertEqual(
            "SEMANTIC_REVIEW_AND_LEDGER_MERGE_REQUIRED",
            result.checkpoint.values["worker_coordination_blocker"],
        )
        self.assertNotEqual("none", result.checkpoint.values["pending_worker_result_digest"])
        checkpoint_before_verify = (self.root / "PROJECT_GLACIER_RESUME.txt").read_bytes()
        verified = self.coordinator.verify_pending_result(
            source_thread_id="worker-task-1",
            packet_text=packet_text(result_packet),
            task_inactive=True,
        )
        self.assertTrue(verified["pending_packet_verified"])
        self.assertEqual(
            result.checkpoint.values["pending_worker_result_digest"],
            verified["canonical_result_digest"],
        )
        self.assertEqual(
            checkpoint_before_verify,
            (self.root / "PROJECT_GLACIER_RESUME.txt").read_bytes(),
        )
        self.assert_error(
            "WORKER_IDENTITY_MISMATCH",
            self.coordinator.verify_pending_result,
            source_thread_id="different-task",
            packet_text=packet_text(result_packet),
            task_inactive=True,
        )
        self.assert_error(
            "TASK_STILL_ACTIVE",
            self.coordinator.verify_pending_result,
            source_thread_id="worker-task-1",
            packet_text=packet_text(result_packet),
            task_inactive=False,
        )
        changed_result = dict(result_packet)
        changed_result["duplicate_risk"] = "Changed after checkpointing."
        self.assert_error(
            "PENDING_RESULT_MISMATCH",
            self.coordinator.verify_pending_result,
            source_thread_id="worker-task-1",
            packet_text=packet_text(changed_result),
            task_inactive=True,
        )

        disposition = self.coordinator.commit_result(
            **self.fence(result.checkpoint),
            disposition="ACCEPTED",
            artifacts_reviewed=False,
        )
        self.assertEqual("ACK_BUILD_PENDING", disposition.checkpoint.values["offline_worker_status"])
        self.assertEqual(
            result_packet["message_id"],
            disposition.checkpoint.values["last_accepted_worker_result_id"],
        )

        ack_planned = self.coordinator.plan_ack(**self.fence(disposition.checkpoint))
        self.assertEqual("ACK_PENDING", ack_planned.checkpoint.values["offline_worker_status"])
        ack_packet = parse_packet(ack_planned.action.as_dict()["arguments"]["prompt"])
        validate_ack(ack_packet)
        self.assertEqual(disposition.checkpoint.version, ack_packet["accepted_state_version"])
        self.assertEqual(disposition.checkpoint.state_hash, ack_packet["accepted_state_hash"])
        self.assertEqual("ACCEPTED", ack_packet["disposition"])

        acked = self.coordinator.record_delivery(
            **self.fence(ack_planned.checkpoint),
            message_id=ack_packet["message_id"],
            delivery_status="ACKNOWLEDGED",
        )
        self.assertEqual("ACK_PENDING", acked.checkpoint.values["offline_worker_status"])
        self.assertEqual("codex_app__read_thread", acked.action["tool"])
        control_completed = self.coordinator.complete_control_turn(
            **self.fence(acked.checkpoint),
            source_thread_id="worker-task-1",
            task_inactive=True,
        )
        self.assertEqual("IDLE", control_completed.checkpoint.values["offline_worker_status"])
        self.assertEqual("none", control_completed.checkpoint.values["active_worker_assignment_id"])
        self.assertEqual("NONE", control_completed.checkpoint.values["coordination_outbox_delivery_status"])

        stop_planned = self.coordinator.plan_stop(
            **self.fence(control_completed.checkpoint),
            reason_code="CLEANUP",
        )
        stop_packet = parse_packet(stop_planned.action.as_dict()["arguments"]["prompt"])
        self.assertEqual("STOP_ALL", stop_packet["type"])
        self.assertEqual("CLEANUP", stop_packet["reason_code"])
        self.assertEqual("STOPPING", stop_planned.checkpoint.values["offline_worker_status"])

        stop_delivered = self.coordinator.record_delivery(
            **self.fence(stop_planned.checkpoint),
            message_id=stop_packet["message_id"],
            delivery_status="ACKNOWLEDGED",
        )
        stop_ack_packet = {
            "type": "STOP_ACK",
            "protocol_version": 2,
            "message_id": "stop-ack-1",
            "reply_to_message_id": stop_packet["message_id"],
            "leadership_epoch": stop_packet["leadership_epoch"],
            "assignment_id": None,
            "children_running": 0,
            "files_written": [],
            "live_actions": "NONE",
        }
        archive_planned = self.coordinator.accept_stop_ack(
            **self.fence(stop_delivered.checkpoint),
            source_thread_id="worker-task-1",
            packet_text=packet_text(stop_ack_packet),
            task_inactive=True,
        )
        self.assertEqual("ARCHIVE_PLANNED", archive_planned.checkpoint.values["offline_worker_status"])
        archive_action = archive_planned.action.as_dict()
        self.assertEqual("codex_app__set_thread_archived", archive_action["tool"])
        self.assertEqual(
            {"threadId": "worker-task-1", "archived": True},
            archive_action["arguments"],
        )

        archive_unknown = self.coordinator.record_archived(
            **self.fence(archive_planned.checkpoint),
            delivery_status="UNKNOWN",
        )
        self.assert_error(
            "DELIVERY_EVIDENCE_REQUIRED",
            self.coordinator.record_archived,
            **self.fence(archive_unknown.checkpoint),
            delivery_status="ACKNOWLEDGED",
        )
        archived = self.coordinator.record_archived(
            **self.fence(archive_unknown.checkpoint),
            delivery_status="ACKNOWLEDGED",
            delivery_evidence_verified=True,
        )
        self.assertEqual("ARCHIVED", archived.checkpoint.values["offline_worker_status"])
        self.assertEqual("NONE", archived.checkpoint.values["offline_worker_expected_response_type"])
        self.assertEqual("ACKNOWLEDGED", archived.checkpoint.values["coordination_outbox_delivery_status"])
        self.assertEqual("WORKER_ARCHIVED", archived.outcome)

        deactivated = self.coordinator.deactivate_pilot(**self.fence(archived.checkpoint))
        self.assertEqual("SINGLE_ROOT", deactivated.checkpoint.values["orchestration_mode"])

    def test_dry_run_creates_neither_checkpoint_lock_nor_outbox(self) -> None:
        initial = self.coordinator.store.read()
        activated = self.coordinator.activate_pilot(
            **self.fence(initial),
            user_authorized_offline_worker=True,
        )
        (self.root / ".project-glacier-orchestrator.lock").unlink()
        original = (self.root / "PROJECT_GLACIER_RESUME.txt").read_bytes()
        transition = self.coordinator.plan_bootstrap(
            expected_version=activated.checkpoint.version,
            expected_hash=activated.checkpoint.state_hash,
            commit=False,
        )
        self.assertFalse(transition.committed)
        self.assertFalse(transition.as_dict()["action_executable"])
        self.assertEqual(original, (self.root / "PROJECT_GLACIER_RESUME.txt").read_bytes())
        self.assertFalse((self.root / ".project-glacier-orchestrator.lock").exists())
        self.assertFalse((self.root / ".project-glacier-orchestrator").exists())

    def test_prompt_reconciliation_validates_before_any_durable_write(self) -> None:
        current = self.coordinator.store.read()
        prompt = self.root / "project_glacier_offline_root_worker_prompt.md"
        prompt.write_text(prompt.read_text(encoding="utf-8") + "\n", encoding="utf-8")
        reconciled = self.coordinator.reconcile_prompt(**self.fence(current))
        self.assertNotEqual(
            current.values["offline_worker_prompt_digest"],
            reconciled.checkpoint.values["offline_worker_prompt_digest"],
        )
        self.assertEqual(
            self.coordinator.store.artifact_digests().worker_prompt,
            reconciled.checkpoint.values["offline_worker_prompt_digest"],
        )

        invalid = reconciled.checkpoint.clone()
        invalid.values["active_worker_assignment_id"] = "partial-assignment"
        invalid.values["canonical_state_hash"] = "0" * 64
        invalid.values["canonical_state_hash"] = invalid.computed_hash()
        resume = self.root / "PROJECT_GLACIER_RESUME.txt"
        resume.write_text(invalid.render(), encoding="utf-8")
        before = resume.read_bytes()
        prompt.write_text(prompt.read_text(encoding="utf-8") + "\n", encoding="utf-8")
        self.assert_error(
            "COORDINATION_STATE_INVALID",
            self.coordinator.reconcile_prompt,
            **self.fence(invalid),
        )
        self.assertEqual(before, resume.read_bytes())

    def test_checkpoint_detects_outbox_tampering_even_if_attacker_rehashes_record(self) -> None:
        activated = self.activate_pilot()
        planned = self.coordinator.plan_bootstrap(**self.fence(activated.checkpoint))
        message_id = planned.checkpoint.values["coordination_outbox_message_id"]
        path = self.coordinator.outbox.directory / f"{message_id}.json"
        document = json.loads(path.read_text(encoding="utf-8"))
        document["payload"] += "\nchanged"
        document["payload_digest"] = payload_digest(document["payload"])
        path.write_bytes(canonical_json_bytes(document))
        self.assert_error("OUTBOX_CHECKPOINT_MISMATCH", self.coordinator.pending_action)

    def test_result_is_rebased_when_noncoordination_live_state_changes(self) -> None:
        activated = self.activate_pilot()
        bootstrap = self.coordinator.plan_bootstrap(**self.fence(activated.checkpoint))
        created = self.coordinator.record_worker_created(
            **self.fence(bootstrap.checkpoint),
            thread_id="worker-task-1",
            host_id="none",
            ambiguity_resolved=False,
        )
        attested = self.coordinator.accept_attestation(
            **self.fence(created.checkpoint),
            source_thread_id="worker-task-1",
            packet_text=packet_text(
                valid_attestation(
                    bootstrap_id=created.checkpoint.values["offline_worker_bootstrap_message_id"],
                    prompt_digest=created.checkpoint.values["offline_worker_prompt_digest"],
                )
            ),
            task_inactive=True,
        )
        assignment_planned = self.coordinator.plan_assignment(
            **self.fence(attested.checkpoint),
            spec=valid_assignment_spec(),
        )
        assignment = parse_packet(assignment_planned.action.as_dict()["arguments"]["prompt"])

        # Simulate a separately checkpointed, acknowledged live-state mutation. This field is not
        # coordinator-owned, so its change must stale the worker snapshot even though the packet's
        # own base hash and artifact digests still match the original assignment.
        changed = assignment_planned.checkpoint.clone()
        changed.values["last_delivery_status"] = "none"
        changed.values["canonical_state_version"] = str(changed.version + 1)
        changed.values["canonical_state_hash"] = "0" * 64
        changed.values["canonical_state_hash"] = changed.computed_hash()
        (self.root / "PROJECT_GLACIER_RESUME.txt").write_text(
            changed.render(),
            encoding="utf-8",
        )
        changed = self.coordinator.store.read()

        assigned = self.coordinator.record_delivery(
            **self.fence(changed),
            message_id=assignment["message_id"],
            delivery_status="ACKNOWLEDGED",
        )
        result = self.coordinator.accept_result(
            **self.fence(assigned.checkpoint),
            source_thread_id="worker-task-1",
            packet_text=packet_text(valid_result_packet(assignment)),
            task_inactive=True,
        )
        self.assertEqual("STALE_RESULT_REBASE_PLANNED", result.outcome)
        self.assertEqual("REBASE_PENDING", result.checkpoint.values["offline_worker_status"])
        rebase = parse_packet(result.action.as_dict()["arguments"]["prompt"])
        self.assertEqual("REBASE_REQUIRED", rebase["type"])
        self.assertEqual("STATE_HASH_MISMATCH", rebase["reason_code"])

    def test_rules_or_ledger_change_blocks_pending_send_and_rebases_old_result(self) -> None:
        attested = self.create_attested_worker()
        planned = self.coordinator.plan_assignment(
            **self.fence(attested.checkpoint),
            spec=valid_assignment_spec(),
        )
        assignment = parse_packet(planned.action.as_dict()["arguments"]["prompt"])
        ledger = self.root / "glacier_experiment_ledger.md"
        ledger.write_text(ledger.read_text(encoding="utf-8") + "\nReviewed canonical change.\n", encoding="utf-8")
        reconciled = self.coordinator.store.update(
            expected_version=planned.checkpoint.version,
            expected_hash=planned.checkpoint.state_hash,
            updates={"worker_coordination_blocker": "none"},
            allowed_fields=MUTABLE_COORDINATION_FIELDS,
            commit=True,
            allow_rules_ledger_reconciliation=True,
        )
        self.assert_error("ASSIGNMENT_SNAPSHOT_STALE", self.coordinator.pending_action)
        assigned = self.coordinator.record_delivery(
            **self.fence(reconciled),
            message_id=assignment["message_id"],
            delivery_status="ACKNOWLEDGED",
        )
        result = self.coordinator.accept_result(
            **self.fence(assigned.checkpoint),
            source_thread_id="worker-task-1",
            packet_text=packet_text(valid_result_packet(assignment)),
            task_inactive=True,
        )
        self.assertEqual("STALE_RESULT_REBASE_PLANNED", result.outcome)
        rebase = parse_packet(result.action.as_dict()["arguments"]["prompt"])
        self.assertEqual("LEDGER_DIGEST_MISMATCH", rebase["reason_code"])
        changed_epoch = result.checkpoint.clone()
        changed_epoch.values["multi_root_leadership_epoch"] = str(
            int(changed_epoch.values["multi_root_leadership_epoch"]) + 1
        )
        changed_epoch.values["canonical_state_version"] = str(changed_epoch.version + 1)
        changed_epoch.values["canonical_state_hash"] = "0" * 64
        changed_epoch.values["canonical_state_hash"] = changed_epoch.computed_hash()
        (self.root / "PROJECT_GLACIER_RESUME.txt").write_text(
            changed_epoch.render(),
            encoding="utf-8",
        )
        self.assert_error("CONTROL_SNAPSHOT_STALE", self.coordinator.pending_action)

    def test_pending_action_rechecks_live_quiescence(self) -> None:
        attested = self.create_attested_worker()
        planned = self.coordinator.plan_assignment(
            **self.fence(attested.checkpoint),
            spec=valid_assignment_spec(),
        )
        changed = planned.checkpoint.clone()
        changed.values["active_case_id"] = "case-active"
        changed.values["active_case_state"] = "STARTED"
        changed.values["canonical_state_version"] = str(changed.version + 1)
        changed.values["canonical_state_hash"] = "0" * 64
        changed.values["canonical_state_hash"] = changed.computed_hash()
        (self.root / "PROJECT_GLACIER_RESUME.txt").write_text(changed.render(), encoding="utf-8")
        self.assert_error("LIVE_STATE_NOT_QUIESCENT", self.coordinator.pending_action)

    def test_pending_assignment_rechecks_leadership_epoch_and_packet_fences(self) -> None:
        attested = self.create_attested_worker()
        planned = self.coordinator.plan_assignment(
            **self.fence(attested.checkpoint),
            spec=valid_assignment_spec(),
        )
        changed = planned.checkpoint.clone()
        changed.values["multi_root_leadership_epoch"] = str(
            int(changed.values["multi_root_leadership_epoch"]) + 1
        )
        changed.values["canonical_state_version"] = str(changed.version + 1)
        changed.values["canonical_state_hash"] = "0" * 64
        changed.values["canonical_state_hash"] = changed.computed_hash()
        (self.root / "PROJECT_GLACIER_RESUME.txt").write_text(changed.render(), encoding="utf-8")
        self.assert_error("ASSIGNMENT_SNAPSHOT_STALE", self.coordinator.pending_action)

    def test_pending_ack_rechecks_current_leadership_epoch(self) -> None:
        planned = self.create_planned_ack()
        changed = planned.checkpoint.clone()
        changed.values["multi_root_leadership_epoch"] = str(
            int(changed.values["multi_root_leadership_epoch"]) + 1
        )
        changed.values["canonical_state_version"] = str(changed.version + 1)
        changed.values["canonical_state_hash"] = "0" * 64
        changed.values["canonical_state_hash"] = changed.computed_hash()
        (self.root / "PROJECT_GLACIER_RESUME.txt").write_text(changed.render(), encoding="utf-8")
        self.assert_error("CONTROL_SNAPSHOT_STALE", self.coordinator.pending_action)

    def test_protocol_reject_requires_a_delivered_pending_turn(self) -> None:
        attested = self.create_attested_worker()
        rejection = {
            "type": "PROTOCOL_REJECT",
            "protocol_version": 2,
            "message_id": "reject-idle",
            "reply_to_message_id": None,
            "assignment_id": None,
            "leadership_epoch": None,
            "reason_code": "OTHER",
            "details": "REJECTED_BY_CLOSED_PROTOCOL",
            "prohibited_data_echoed": False,
            "tool_calls_attempted": [],
            "files_accessed": [],
            "files_written": [],
            "peer_messages_sent": [],
        }
        self.assert_error(
            "LIFECYCLE_TRANSITION_INVALID",
            self.coordinator.accept_protocol_reject,
            **self.fence(attested.checkpoint),
            source_thread_id="worker-task-1",
            packet_text=packet_text(rejection),
            task_inactive=True,
        )

    def test_stop_protocol_reject_is_recordable_during_adverse_live_recovery(self) -> None:
        attested = self.create_attested_worker()
        adverse = attested.checkpoint.clone()
        adverse.values.update(
            {
                "checkpoint_reconciliation_required": "true",
                "global_mutation_lock": "broker-recovery-lock",
                "last_delivery_status": "UNKNOWN",
            }
        )
        adverse.values["canonical_state_version"] = str(adverse.version + 1)
        adverse.values["canonical_state_hash"] = "0" * 64
        adverse.values["canonical_state_hash"] = adverse.computed_hash()
        (self.root / "PROJECT_GLACIER_RESUME.txt").write_text(adverse.render(), encoding="utf-8")
        adverse = self.coordinator.store.read()
        stop = self.coordinator.plan_stop(
            **self.fence(adverse),
            reason_code="DELIVERY_LOCK",
        )
        stop_packet = parse_packet(stop.action.as_dict()["arguments"]["prompt"])
        delivered = self.coordinator.record_delivery(
            **self.fence(stop.checkpoint),
            message_id=stop_packet["message_id"],
            delivery_status="ACKNOWLEDGED",
        )
        rejection = {
            "type": "PROTOCOL_REJECT",
            "protocol_version": 2,
            "message_id": "stop-protocol-reject",
            "reply_to_message_id": stop_packet["message_id"],
            "assignment_id": stop_packet["assignment_id"],
            "leadership_epoch": stop_packet["leadership_epoch"],
            "reason_code": "OTHER",
            "details": "REJECTED_BY_CLOSED_PROTOCOL",
            "prohibited_data_echoed": False,
            "tool_calls_attempted": [],
            "files_accessed": [],
            "files_written": [],
            "peer_messages_sent": [],
        }
        blocked = self.coordinator.accept_protocol_reject(
            **self.fence(delivered.checkpoint),
            source_thread_id="worker-task-1",
            packet_text=packet_text(rejection),
            task_inactive=True,
        )
        self.assertEqual("PROTOCOL_BLOCKED", blocked.checkpoint.values["offline_worker_status"])
        self.assertEqual("NONE", blocked.checkpoint.values["coordination_outbox_delivery_status"])
        cleanup = self.coordinator.plan_stop(
            **self.fence(blocked.checkpoint),
            reason_code="CLEANUP",
        )
        self.assertEqual("STOPPING", cleanup.checkpoint.values["offline_worker_status"])
        self.assertEqual(cleanup.action.as_dict(), self.coordinator.pending_action()["action"])

    def test_emergency_stop_requires_separate_cleanup_handshake_before_archive(self) -> None:
        attested = self.create_attested_worker()
        emergency = self.coordinator.plan_stop(
            **self.fence(attested.checkpoint),
            reason_code="USER_STOP",
        )
        emergency_packet = parse_packet(emergency.action.as_dict()["arguments"]["prompt"])
        delivered = self.coordinator.record_delivery(
            **self.fence(emergency.checkpoint),
            message_id=emergency_packet["message_id"],
            delivery_status="ACKNOWLEDGED",
        )
        emergency_ack = {
            "type": "STOP_ACK",
            "protocol_version": 2,
            "message_id": "emergency-stop-ack",
            "reply_to_message_id": emergency_packet["message_id"],
            "leadership_epoch": emergency_packet["leadership_epoch"],
            "assignment_id": None,
            "children_running": 0,
            "files_written": [],
            "live_actions": "NONE",
        }
        fenced = self.coordinator.accept_stop_ack(
            **self.fence(delivered.checkpoint),
            source_thread_id="worker-task-1",
            packet_text=packet_text(emergency_ack),
            task_inactive=True,
        )
        self.assertEqual("STOPPED_FENCED", fenced.checkpoint.values["offline_worker_status"])
        self.assertIsNone(fenced.action)
        self.assert_error(
            "CLEANUP_HANDSHAKE_REQUIRED",
            self.coordinator.plan_stop,
            **self.fence(fenced.checkpoint),
            reason_code="USER_STOP",
        )
        cleanup = self.coordinator.plan_stop(
            **self.fence(fenced.checkpoint),
            reason_code="CLEANUP",
        )
        cleanup_packet = parse_packet(cleanup.action.as_dict()["arguments"]["prompt"])
        cleanup_delivered = self.coordinator.record_delivery(
            **self.fence(cleanup.checkpoint),
            message_id=cleanup_packet["message_id"],
            delivery_status="ACKNOWLEDGED",
        )
        cleanup_ack = {
            **emergency_ack,
            "message_id": "cleanup-stop-ack",
            "reply_to_message_id": cleanup_packet["message_id"],
        }
        archive = self.coordinator.accept_stop_ack(
            **self.fence(cleanup_delivered.checkpoint),
            source_thread_id="worker-task-1",
            packet_text=packet_text(cleanup_ack),
            task_inactive=True,
        )
        self.assertEqual("ARCHIVE_PLANNED", archive.checkpoint.values["offline_worker_status"])
        self.assertEqual("codex_app__set_thread_archived", archive.action.as_dict()["tool"])

    def test_fail_safe_stop_and_archive_survive_adverse_live_recovery_state(self) -> None:
        attested = self.create_attested_worker()
        adverse = attested.checkpoint.clone()
        adverse.values.update(
            {
                "checkpoint_reconciliation_required": "true",
                "global_mutation_lock": "broker-recovery-lock",
                "active_case_id": "case-with-unknown-delivery",
                "active_case_state": "DELIVERY_UNKNOWN",
                "prepared_payload_hash": "prepared-but-not-replayable",
                "submission_queue": "frozen",
                "last_delivery_status": "UNKNOWN",
            }
        )
        adverse.values["canonical_state_version"] = str(adverse.version + 1)
        adverse.values["canonical_state_hash"] = "0" * 64
        adverse.values["canonical_state_hash"] = adverse.computed_hash()
        (self.root / "PROJECT_GLACIER_RESUME.txt").write_text(adverse.render(), encoding="utf-8")
        adverse = self.coordinator.store.read()
        self.assert_error(
            "CHECKPOINT_RECONCILIATION_REQUIRED",
            self.coordinator.plan_assignment,
            **self.fence(adverse),
            spec=valid_assignment_spec(),
        )

        emergency = self.coordinator.plan_stop(
            **self.fence(adverse),
            reason_code="DELIVERY_LOCK",
        )
        self.assertEqual(emergency.action.as_dict(), self.coordinator.pending_action()["action"])
        emergency_packet = parse_packet(emergency.action.as_dict()["arguments"]["prompt"])
        delivered = self.coordinator.record_delivery(
            **self.fence(emergency.checkpoint),
            message_id=emergency_packet["message_id"],
            delivery_status="ACKNOWLEDGED",
        )
        emergency_ack = {
            "type": "STOP_ACK",
            "protocol_version": 2,
            "message_id": "delivery-lock-stop-ack",
            "reply_to_message_id": emergency_packet["message_id"],
            "leadership_epoch": emergency_packet["leadership_epoch"],
            "assignment_id": None,
            "children_running": 0,
            "files_written": [],
            "live_actions": "NONE",
        }
        fenced = self.coordinator.accept_stop_ack(
            **self.fence(delivered.checkpoint),
            source_thread_id="worker-task-1",
            packet_text=packet_text(emergency_ack),
            task_inactive=True,
        )
        self.assertEqual("STOPPED_FENCED", fenced.checkpoint.values["offline_worker_status"])

        cleanup = self.coordinator.plan_stop(
            **self.fence(fenced.checkpoint),
            reason_code="CLEANUP",
        )
        self.assertEqual(cleanup.action.as_dict(), self.coordinator.pending_action()["action"])
        cleanup_packet = parse_packet(cleanup.action.as_dict()["arguments"]["prompt"])
        cleanup_delivered = self.coordinator.record_delivery(
            **self.fence(cleanup.checkpoint),
            message_id=cleanup_packet["message_id"],
            delivery_status="ACKNOWLEDGED",
        )
        cleanup_ack = {
            **emergency_ack,
            "message_id": "cleanup-under-recovery-stop-ack",
            "reply_to_message_id": cleanup_packet["message_id"],
        }
        archive = self.coordinator.accept_stop_ack(
            **self.fence(cleanup_delivered.checkpoint),
            source_thread_id="worker-task-1",
            packet_text=packet_text(cleanup_ack),
            task_inactive=True,
        )
        self.assertEqual("ARCHIVE_PLANNED", archive.checkpoint.values["offline_worker_status"])
        self.assertEqual(archive.action.as_dict(), self.coordinator.pending_action()["action"])
        archived = self.coordinator.record_archived(
            **self.fence(archive.checkpoint),
            delivery_status="ACKNOWLEDGED",
        )
        self.assertEqual("ARCHIVED", archived.checkpoint.values["offline_worker_status"])
        deactivated = self.coordinator.deactivate_pilot(**self.fence(archived.checkpoint))
        self.assertEqual("SINGLE_ROOT", deactivated.checkpoint.values["orchestration_mode"])

    def test_unknown_creation_delivery_is_fail_closed_and_not_replayable(self) -> None:
        activated = self.activate_pilot()
        planned = self.coordinator.plan_bootstrap(**self.fence(activated.checkpoint))
        unknown = self.coordinator.record_delivery(
            **self.fence(planned.checkpoint),
            message_id=planned.checkpoint.values["coordination_outbox_message_id"],
            delivery_status="UNKNOWN",
        )
        self.assertEqual("UNKNOWN", unknown.checkpoint.values["coordination_outbox_delivery_status"])
        self.assert_error("OUTBOX_DELIVERY_UNKNOWN", self.coordinator.pending_action)
        diagnostics = self.coordinator.unknown_delivery_diagnostics()
        self.assertIn("do not resend", diagnostics["warning"].lower())
        self.assertEqual("codex_app__list_threads", diagnostics["diagnostic_actions"][0]["tool"])
        self.assert_error(
            "CREATE_RETRY_UNSAFE",
            self.coordinator.requeue_known_nondelivery,
            **self.fence(unknown.checkpoint),
            nondelivery_verified=True,
            task_inactive=True,
        )
        self.assert_error(
            "CREATE_AMBIGUITY_UNRESOLVED",
            self.coordinator.record_worker_created,
            **self.fence(unknown.checkpoint),
            thread_id="worker-task-resolved",
            host_id="none",
            ambiguity_resolved=False,
        )
        resolved = self.coordinator.record_worker_created(
            **self.fence(unknown.checkpoint),
            thread_id="worker-task-resolved",
            host_id="none",
            ambiguity_resolved=True,
        )
        self.assertEqual("ATTESTING", resolved.checkpoint.values["offline_worker_status"])

    def test_verified_message_nondelivery_requeues_only_the_exact_outbox_record(self) -> None:
        activated = self.activate_pilot()
        bootstrap = self.coordinator.plan_bootstrap(**self.fence(activated.checkpoint))
        created = self.coordinator.record_worker_created(
            **self.fence(bootstrap.checkpoint),
            thread_id="worker-task-1",
            host_id="none",
            ambiguity_resolved=False,
        )
        attested = self.coordinator.accept_attestation(
            **self.fence(created.checkpoint),
            source_thread_id="worker-task-1",
            packet_text=packet_text(
                valid_attestation(
                    bootstrap_id=created.checkpoint.values["offline_worker_bootstrap_message_id"],
                    prompt_digest=created.checkpoint.values["offline_worker_prompt_digest"],
                )
            ),
            task_inactive=True,
        )
        planned = self.coordinator.plan_assignment(
            **self.fence(attested.checkpoint),
            spec=valid_assignment_spec(),
        )
        original_action = planned.action.as_dict()
        message_id = planned.checkpoint.values["coordination_outbox_message_id"]
        unknown = self.coordinator.record_delivery(
            **self.fence(planned.checkpoint),
            message_id=message_id,
            delivery_status="UNKNOWN",
        )
        self.assert_error(
            "NONDELIVERY_EVIDENCE_REQUIRED",
            self.coordinator.requeue_known_nondelivery,
            **self.fence(unknown.checkpoint),
            nondelivery_verified=False,
            task_inactive=True,
        )
        self.assert_error(
            "TASK_STILL_ACTIVE",
            self.coordinator.requeue_known_nondelivery,
            **self.fence(unknown.checkpoint),
            nondelivery_verified=True,
            task_inactive=False,
        )
        requeued = self.coordinator.requeue_known_nondelivery(
            **self.fence(unknown.checkpoint),
            nondelivery_verified=True,
            task_inactive=True,
        )
        self.assertEqual("PLANNED", requeued.checkpoint.values["coordination_outbox_delivery_status"])
        replayed_action = self.coordinator.pending_action()["action"]
        self.assertEqual(original_action["action_id"], replayed_action["action_id"])
        self.assertEqual(original_action["tool"], replayed_action["tool"])
        self.assertEqual(original_action["arguments"], replayed_action["arguments"])
        self.assertEqual(
            original_action["checkpoint_precondition"]["outbox_payload_digest"],
            replayed_action["checkpoint_precondition"]["outbox_payload_digest"],
        )

    def test_unknown_message_ack_requires_explicit_delivery_evidence(self) -> None:
        attested = self.create_attested_worker()
        planned = self.coordinator.plan_assignment(
            **self.fence(attested.checkpoint),
            spec=valid_assignment_spec(),
        )
        message_id = planned.checkpoint.values["coordination_outbox_message_id"]
        unknown = self.coordinator.record_delivery(
            **self.fence(planned.checkpoint),
            message_id=message_id,
            delivery_status="UNKNOWN",
        )
        self.assert_error(
            "DELIVERY_EVIDENCE_REQUIRED",
            self.coordinator.record_delivery,
            **self.fence(unknown.checkpoint),
            message_id=message_id,
            delivery_status="ACKNOWLEDGED",
        )
        acknowledged = self.coordinator.record_delivery(
            **self.fence(unknown.checkpoint),
            message_id=message_id,
            delivery_status="ACKNOWLEDGED",
            delivery_evidence_verified=True,
        )
        self.assertEqual("ASSIGNED", acknowledged.checkpoint.values["offline_worker_status"])

    def test_plain_single_root_mode_cannot_activate_worker_implicitly(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = make_workspace(Path(temporary))
            coordinator = Coordinator(root, id_factory=DeterministicIds())
            current = coordinator.store.read()
            self.assert_error(
                "MULTI_ROOT_NOT_ACTIVATED",
                coordinator.plan_bootstrap,
                expected_version=current.version,
                expected_hash=current.state_hash,
                commit=True,
            )

    def test_stop_ack_needs_independent_inactive_observation(self) -> None:
        # Build only the minimal lifecycle prefix needed to reach an acknowledged STOP_ALL.
        activated = self.activate_pilot()
        bootstrap = self.coordinator.plan_bootstrap(**self.fence(activated.checkpoint))
        created = self.coordinator.record_worker_created(
            **self.fence(bootstrap.checkpoint),
            thread_id="worker-task-1",
            host_id="none",
            ambiguity_resolved=False,
        )
        attested = self.coordinator.accept_attestation(
            **self.fence(created.checkpoint),
            source_thread_id="worker-task-1",
            packet_text=packet_text(
                valid_attestation(
                    bootstrap_id=created.checkpoint.values["offline_worker_bootstrap_message_id"],
                    prompt_digest=created.checkpoint.values["offline_worker_prompt_digest"],
                )
            ),
            task_inactive=True,
        )
        stop = self.coordinator.plan_stop(
            **self.fence(attested.checkpoint),
            reason_code="CLEANUP",
        )
        stop_packet = parse_packet(stop.action.as_dict()["arguments"]["prompt"])
        delivered = self.coordinator.record_delivery(
            **self.fence(stop.checkpoint),
            message_id=stop_packet["message_id"],
            delivery_status="ACKNOWLEDGED",
        )
        ack = {
            "type": "STOP_ACK",
            "protocol_version": 2,
            "message_id": "stop-ack-2",
            "reply_to_message_id": stop_packet["message_id"],
            "leadership_epoch": stop_packet["leadership_epoch"],
            "assignment_id": None,
            "children_running": 0,
            "files_written": [],
            "live_actions": "NONE",
        }
        self.assert_error(
            "TASK_STILL_ACTIVE",
            self.coordinator.accept_stop_ack,
            **self.fence(delivered.checkpoint),
            source_thread_id="worker-task-1",
            packet_text=packet_text(ack),
            task_inactive=False,
        )


if __name__ == "__main__":
    unittest.main()

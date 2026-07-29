"""Deterministic lifecycle for one isolated, projectless offline worker task."""

from __future__ import annotations

import json
import secrets
import tomllib
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .canonical import MAX_SAFE_INTEGER, canonical_json_bytes, canonical_json_digest
from .checkpoint import ResumeDocument, ResumeStore, WorkspacePaths, secure_read_text
from .codex_actions import (
    TaskAction,
    archive_thread_action,
    create_thread_action,
    list_threads_action,
    read_thread_action,
    send_message_action,
)
from .errors import OrchestratorError
from .outbox import OutboxRecord, OutboxStore
from .protocol import (
    DIGEST_PATTERN,
    ID_PATTERN,
    PROTOCOL_VERSION,
    REBASE_REASONS,
    WORKER_PROMPT_VERSION,
    ResultExpectation,
    build_ack,
    build_assignment,
    build_attest_request,
    build_rebase,
    build_stop,
    parse_packet,
    validate_attestation,
    validate_closed_packet,
    validate_protocol_reject,
    validate_result_against_expectation,
    validate_result_packet,
    validate_stop_ack,
)

NONE = "none"
REPLAY_WINDOW_LIMIT = 256
ORCHESTRATION_MODE = "MULTI_ROOT_PILOT"

WORKER_STATUSES = frozenset(
    {
        "UNREGISTERED",
        "CREATING",
        "ATTESTING",
        "IDLE",
        "ASSIGNMENT_PLANNED",
        "ASSIGNED",
        "RESULT_VALIDATED",
        "ACK_BUILD_PENDING",
        "ACK_PENDING",
        "REBASE_PENDING",
        "STOPPING",
        "STOPPED_FENCED",
        "ARCHIVE_PLANNED",
        "ARCHIVED",
        "PROTOCOL_BLOCKED",
    }
)
OUTBOX_STATUSES = frozenset({"NONE", "PLANNED", "ACKNOWLEDGED", "UNKNOWN", "REJECTED"})
EXPECTED_RESPONSES = frozenset({"NONE", "OFFLINE_ROOT_ATTESTATION", "RESULT_PACKET", "STOP_ACK"})
OUTBOX_TYPES = frozenset(
    {
        "CREATE_THREAD",
        "ASSIGNMENT_PACKET",
        "ACK",
        "REBASE_REQUIRED",
        "STOP_ALL",
        "ARCHIVE_THREAD",
    }
)

REQUIRED_COORDINATION_KEYS = (
    "orchestration_mode",
    "multi_root_protocol_version",
    "canonical_state_version",
    "canonical_state_hash",
    "checkpoint_generated_at",
    "checkpoint_reconciliation_required",
    "global_mutation_lock",
    "multi_root_leadership_epoch",
    "multi_root_replay_window_size",
    "multi_root_processed_message_ids",
    "multi_root_processed_result_ids",
    "rules_digest",
    "ledger_digest",
    "offline_worker_thread_id",
    "offline_worker_host_id",
    "offline_worker_prompt_version",
    "offline_worker_prompt_digest",
    "offline_worker_bootstrap_message_id",
    "offline_worker_attested_prompt_version",
    "offline_worker_attested_prompt_digest",
    "offline_worker_pending_message_id",
    "offline_worker_expected_response_type",
    "offline_worker_status",
    "offline_worker_last_ack_version",
    "next_worker_assignment_sequence",
    "active_worker_assignment_id",
    "active_worker_assignment_message_id",
    "active_worker_assignment_base_version",
    "active_worker_assignment_base_hash",
    "active_worker_assignment_rules_digest",
    "active_worker_assignment_ledger_digest",
    "active_worker_assignment_live_state_digest",
    "pending_worker_result_id",
    "pending_worker_result_digest",
    "pending_worker_result_assignment_id",
    "last_accepted_worker_result_id",
    "last_worker_result_disposition",
    "coordination_outbox_message_id",
    "coordination_outbox_message_type",
    "coordination_outbox_payload_digest",
    "coordination_outbox_delivery_status",
    "worker_coordination_blocker",
)

MUTABLE_COORDINATION_FIELDS = frozenset(REQUIRED_COORDINATION_KEYS) - {
    "orchestration_mode",
    "multi_root_protocol_version",
    "canonical_state_version",
    "canonical_state_hash",
    "checkpoint_generated_at",
    "rules_digest",
    "ledger_digest",
}
MODE_MUTABLE_FIELDS = MUTABLE_COORDINATION_FIELDS | {"orchestration_mode"}


@dataclass(frozen=True)
class Transition:
    committed: bool
    checkpoint: ResumeDocument
    action: TaskAction | dict[str, Any] | None = None
    outcome: str = "PLANNED"

    def as_dict(self) -> dict[str, Any]:
        value: dict[str, Any] = {
            "ok": True,
            "committed": self.committed,
            "outcome": self.outcome,
            "checkpoint": {
                "canonical_state_version": self.checkpoint.version,
                "canonical_state_hash": self.checkpoint.state_hash,
                "offline_worker_status": self.checkpoint.values["offline_worker_status"],
                "outbox_delivery_status": self.checkpoint.values["coordination_outbox_delivery_status"],
            },
            "action_executable": self.committed and self.action is not None,
        }
        if self.action is not None:
            value["action"] = self.action.as_dict() if isinstance(self.action, TaskAction) else self.action
        if not self.committed and self.action is not None:
            value["warning"] = "Dry-run only: do not execute the displayed task action."
        return value


def _parse_nonnegative(value: str, *, field: str, minimum: int = 0) -> int:
    if not value.isascii() or not value.isdigit():
        raise OrchestratorError("COORDINATION_STATE_INVALID", f"The {field} checkpoint field is invalid.")
    parsed = int(value)
    if parsed < minimum or parsed > MAX_SAFE_INTEGER:
        raise OrchestratorError("COORDINATION_STATE_INVALID", f"The {field} checkpoint field is out of range.")
    return parsed


def _require_identifier(value: str, *, allow_none: bool = False) -> None:
    if allow_none and value == NONE:
        return
    if ID_PATTERN.fullmatch(value) is None:
        raise OrchestratorError("COORDINATION_STATE_INVALID", "A checkpoint identifier has an invalid format.")


def _require_digest(value: str, *, allow_none: bool = False) -> None:
    if allow_none and value == NONE:
        return
    if DIGEST_PATTERN.fullmatch(value) is None:
        raise OrchestratorError("COORDINATION_STATE_INVALID", "A checkpoint digest has an invalid format.")


def _decode_replay(document: ResumeDocument, key: str, *, maximum: int) -> list[str]:
    return document.json_array(key, maximum=maximum)


def validate_coordination_state(document: ResumeDocument) -> None:
    values = document.values
    if values["orchestration_mode"] not in {"SINGLE_ROOT", ORCHESTRATION_MODE}:
        raise OrchestratorError("COORDINATION_STATE_INVALID", "The orchestration mode is invalid.")
    if values["multi_root_protocol_version"] != str(PROTOCOL_VERSION):
        raise OrchestratorError("PROTOCOL_VERSION_MISMATCH", "The checkpoint protocol version is unsupported.")
    if values["offline_worker_prompt_version"] != WORKER_PROMPT_VERSION:
        raise OrchestratorError("PROMPT_VERSION_MISMATCH", "The checkpoint worker prompt version is unsupported.")
    for key in (
        "rules_digest",
        "ledger_digest",
        "offline_worker_prompt_digest",
    ):
        _require_digest(values[key])
    for key in (
        "offline_worker_attested_prompt_digest",
        "active_worker_assignment_base_hash",
        "active_worker_assignment_rules_digest",
        "active_worker_assignment_ledger_digest",
        "active_worker_assignment_live_state_digest",
        "pending_worker_result_digest",
        "coordination_outbox_payload_digest",
    ):
        _require_digest(values[key], allow_none=True)
    epoch = _parse_nonnegative(values["multi_root_leadership_epoch"], field="leadership epoch")
    replay_size = _parse_nonnegative(
        values["multi_root_replay_window_size"], field="replay window size", minimum=1
    )
    if replay_size > REPLAY_WINDOW_LIMIT:
        raise OrchestratorError("COORDINATION_STATE_INVALID", "The replay window exceeds the implementation limit.")
    message_ids = _decode_replay(document, "multi_root_processed_message_ids", maximum=replay_size)
    result_digests = _decode_replay(document, "multi_root_processed_result_ids", maximum=replay_size)
    for message_id in message_ids:
        _require_identifier(message_id)
    for digest in result_digests:
        _require_digest(digest)
    _parse_nonnegative(values["next_worker_assignment_sequence"], field="assignment sequence", minimum=1)
    for key in (
        "offline_worker_thread_id",
        "offline_worker_host_id",
        "offline_worker_bootstrap_message_id",
        "offline_worker_pending_message_id",
        "active_worker_assignment_id",
        "active_worker_assignment_message_id",
        "pending_worker_result_id",
        "pending_worker_result_assignment_id",
        "last_accepted_worker_result_id",
        "coordination_outbox_message_id",
    ):
        _require_identifier(values[key], allow_none=True)
    for key in (
        "offline_worker_last_ack_version",
        "active_worker_assignment_base_version",
    ):
        if values[key] != NONE:
            _parse_nonnegative(values[key], field=key)
    status = values["offline_worker_status"]
    if status not in WORKER_STATUSES:
        raise OrchestratorError("COORDINATION_STATE_INVALID", "The worker lifecycle status is invalid.")
    outbox_status = values["coordination_outbox_delivery_status"]
    if outbox_status not in OUTBOX_STATUSES:
        raise OrchestratorError("COORDINATION_STATE_INVALID", "The outbox status is invalid.")
    expected_response = values["offline_worker_expected_response_type"]
    if expected_response not in EXPECTED_RESPONSES:
        raise OrchestratorError("COORDINATION_STATE_INVALID", "The expected response type is invalid.")
    outbox_type = values["coordination_outbox_message_type"]
    if outbox_type != NONE and outbox_type not in OUTBOX_TYPES:
        raise OrchestratorError("COORDINATION_STATE_INVALID", "The outbox message type is invalid.")
    if outbox_status == "NONE":
        if any(
            values[key] != NONE
            for key in (
                "coordination_outbox_message_id",
                "coordination_outbox_message_type",
                "coordination_outbox_payload_digest",
            )
        ):
            raise OrchestratorError("COORDINATION_STATE_INVALID", "An empty outbox has residual message metadata.")
    elif any(
        values[key] == NONE
        for key in (
            "coordination_outbox_message_id",
            "coordination_outbox_message_type",
            "coordination_outbox_payload_digest",
        )
    ):
        raise OrchestratorError("COORDINATION_STATE_INVALID", "A nonempty outbox is missing message metadata.")
    active_values = (
        values["active_worker_assignment_id"],
        values["active_worker_assignment_message_id"],
        values["active_worker_assignment_base_version"],
        values["active_worker_assignment_base_hash"],
        values["active_worker_assignment_rules_digest"],
        values["active_worker_assignment_ledger_digest"],
        values["active_worker_assignment_live_state_digest"],
    )
    if any(value == NONE for value in active_values) and not all(value == NONE for value in active_values):
        raise OrchestratorError("COORDINATION_STATE_INVALID", "Active assignment metadata is only partially populated.")
    result_values = (
        values["pending_worker_result_id"],
        values["pending_worker_result_digest"],
        values["pending_worker_result_assignment_id"],
    )
    if any(value == NONE for value in result_values) and not all(value == NONE for value in result_values):
        raise OrchestratorError("COORDINATION_STATE_INVALID", "Pending result metadata is only partially populated.")
    if values["offline_worker_attested_prompt_version"] not in {NONE, WORKER_PROMPT_VERSION}:
        raise OrchestratorError("COORDINATION_STATE_INVALID", "The attested prompt version is invalid.")
    if values["offline_worker_attested_prompt_version"] == NONE:
        if values["offline_worker_attested_prompt_digest"] != NONE:
            raise OrchestratorError("COORDINATION_STATE_INVALID", "Attested prompt metadata is incomplete.")
    elif values["offline_worker_attested_prompt_digest"] == NONE:
        raise OrchestratorError("COORDINATION_STATE_INVALID", "Attested prompt metadata is incomplete.")
    if status in {"UNREGISTERED", "CREATING"}:
        if values["offline_worker_thread_id"] != NONE:
            raise OrchestratorError("COORDINATION_STATE_INVALID", "A not-yet-created worker has a task ID.")
    elif values["offline_worker_thread_id"] == NONE:
        raise OrchestratorError("COORDINATION_STATE_INVALID", "A created worker lifecycle state has no task ID.")
    active_required = {
        "ASSIGNMENT_PLANNED",
        "ASSIGNED",
        "RESULT_VALIDATED",
        "ACK_BUILD_PENDING",
        "ACK_PENDING",
        "REBASE_PENDING",
    }
    if status in active_required and all(value == NONE for value in active_values):
        raise OrchestratorError("COORDINATION_STATE_INVALID", "The lifecycle requires an active assignment.")
    if status in {"RESULT_VALIDATED", "ACK_BUILD_PENDING", "ACK_PENDING"} and all(
        value == NONE for value in result_values
    ):
        raise OrchestratorError("COORDINATION_STATE_INVALID", "The lifecycle requires a pending result.")
    if status in {"UNREGISTERED", "IDLE", "ARCHIVED"}:
        if not all(value == NONE for value in active_values + result_values):
            raise OrchestratorError("COORDINATION_STATE_INVALID", "An inactive worker has residual assignment state.")
    if status == "IDLE" and (
        expected_response != "NONE" or values["offline_worker_pending_message_id"] != NONE
    ):
        raise OrchestratorError("COORDINATION_STATE_INVALID", "An idle worker is still awaiting a response.")
    if status == "ATTESTING" and expected_response != "OFFLINE_ROOT_ATTESTATION":
        raise OrchestratorError("COORDINATION_STATE_INVALID", "Attestation state has the wrong response fence.")
    if status in {"ASSIGNMENT_PLANNED", "ASSIGNED"} and expected_response != "RESULT_PACKET":
        raise OrchestratorError("COORDINATION_STATE_INVALID", "Assignment state has the wrong response fence.")
    if status == "STOPPING" and expected_response != "STOP_ACK":
        raise OrchestratorError("COORDINATION_STATE_INVALID", "Stop state has the wrong response fence.")
    if status == "STOPPED_FENCED" and (
        expected_response != "NONE" or values["offline_worker_pending_message_id"] != NONE
    ):
        raise OrchestratorError("COORDINATION_STATE_INVALID", "A fenced worker still has a pending response.")
    if epoch == 0 and status not in {"UNREGISTERED", "ARCHIVED"}:
        raise OrchestratorError("COORDINATION_STATE_INVALID", "An active worker must have a nonzero leadership epoch.")
    expected_outbox: dict[str, tuple[str, frozenset[str]]] = {
        "CREATING": ("CREATE_THREAD", frozenset({"PLANNED", "UNKNOWN"})),
        "ATTESTING": ("CREATE_THREAD", frozenset({"ACKNOWLEDGED"})),
        "ASSIGNMENT_PLANNED": (
            "ASSIGNMENT_PACKET",
            frozenset({"PLANNED", "UNKNOWN", "REJECTED"}),
        ),
        "ASSIGNED": ("ASSIGNMENT_PACKET", frozenset({"ACKNOWLEDGED"})),
        "ACK_PENDING": ("ACK", frozenset({"PLANNED", "ACKNOWLEDGED", "UNKNOWN", "REJECTED"})),
        "REBASE_PENDING": (
            "REBASE_REQUIRED",
            frozenset({"PLANNED", "ACKNOWLEDGED", "UNKNOWN", "REJECTED"}),
        ),
        "STOPPING": (
            "STOP_ALL",
            frozenset({"PLANNED", "ACKNOWLEDGED", "UNKNOWN", "REJECTED"}),
        ),
        "ARCHIVE_PLANNED": (
            "ARCHIVE_THREAD",
            frozenset({"PLANNED", "UNKNOWN", "REJECTED"}),
        ),
        "ARCHIVED": ("ARCHIVE_THREAD", frozenset({"ACKNOWLEDGED"})),
    }
    if status in expected_outbox:
        required_type, allowed_statuses = expected_outbox[status]
        if outbox_type != required_type or outbox_status not in allowed_statuses:
            raise OrchestratorError("COORDINATION_STATE_INVALID", "The lifecycle and durable outbox disagree.")
        if status != "ARCHIVED" and values["offline_worker_pending_message_id"] != values["coordination_outbox_message_id"]:
            raise OrchestratorError("COORDINATION_STATE_INVALID", "The pending response fence and outbox identity disagree.")
    elif outbox_status != "NONE":
        raise OrchestratorError("COORDINATION_STATE_INVALID", "This lifecycle state requires an empty outbox.")
    if status in {"CREATING", "ATTESTING"} and (
        values["offline_worker_bootstrap_message_id"] != values["offline_worker_pending_message_id"]
    ):
        raise OrchestratorError("COORDINATION_STATE_INVALID", "The bootstrap response fence is inconsistent.")
    if status in {"ASSIGNMENT_PLANNED", "ASSIGNED"} and (
        values["active_worker_assignment_message_id"] != values["offline_worker_pending_message_id"]
    ):
        raise OrchestratorError("COORDINATION_STATE_INVALID", "The assignment response fence is inconsistent.")


class Coordinator:
    """Plan and record coordination transitions without invoking Codex or Glacier tools."""

    def __init__(
        self,
        workspace_root: Path,
        *,
        id_factory: Callable[[str], str] | None = None,
    ) -> None:
        self.paths = WorkspacePaths.from_root(workspace_root)
        self.store = ResumeStore(
            self.paths,
            required_keys=REQUIRED_COORDINATION_KEYS,
            state_validator=validate_coordination_state,
        )
        self.outbox = OutboxStore(self.paths.root)
        self._id_factory = id_factory or self._random_id

    @staticmethod
    def _random_id(prefix: str) -> str:
        return f"{prefix}-{secrets.token_hex(12)}"

    def _new_id(self, prefix: str) -> str:
        value = self._id_factory(prefix)
        if ID_PATTERN.fullmatch(value) is None or ":" in value:
            raise OrchestratorError("GENERATED_ID_INVALID", "The generated coordination ID is invalid.")
        return value

    def _artifacts_match(self, document: ResumeDocument) -> bool:
        digests = self.store.artifact_digests()
        return (
            document.values["rules_digest"] == digests.rules
            and document.values["ledger_digest"] == digests.ledger
            and document.values["offline_worker_prompt_digest"] == digests.worker_prompt
        )

    def _read_operational(self, *, allow_rules_ledger_drift: bool = False) -> ResumeDocument:
        document = self.store.read()
        digests = self.store.artifact_digests()
        if document.values["offline_worker_prompt_digest"] != digests.worker_prompt:
            raise OrchestratorError("WORKER_PROMPT_DRIFT", "The worker prompt differs from the checkpointed protocol artifact.")
        rules_ledger_match = (
            document.values["rules_digest"] == digests.rules
            and document.values["ledger_digest"] == digests.ledger
        )
        if not rules_ledger_match and not allow_rules_ledger_drift:
            raise OrchestratorError("CANONICAL_ARTIFACT_DRIFT", "Canonical artifact reconciliation is required.")
        return document

    @staticmethod
    def _require_expected(document: ResumeDocument, *, version: int, state_hash: str) -> None:
        if document.version != version or document.state_hash != state_hash:
            raise OrchestratorError("CHECKPOINT_CAS_CONFLICT", "The supplied checkpoint fence is stale.")

    @staticmethod
    def _require_task_lifecycle_safe(
        document: ResumeDocument,
        *,
        allow_unknown_outbox: bool = False,
    ) -> None:
        """Allow recording and fail-safe shutdown without opening a work gate.

        Live Glacier ambiguity and reconciliation locks must block new offline work and result
        ingestion, but they must not orphan a task whose already planned action needs to be
        recorded or whose worker must be stopped. The hashed checkpoint and coordination state
        have already been validated by ``ResumeStore.read``; this narrower gate retains the
        explicit pilot-mode and Codex-outbox delivery fences.
        """

        values = document.values
        if values["orchestration_mode"] != ORCHESTRATION_MODE:
            raise OrchestratorError("MULTI_ROOT_NOT_ACTIVATED", "The checkpoint has not explicitly activated multi-root pilot mode.")
        if not allow_unknown_outbox and values["coordination_outbox_delivery_status"] == "UNKNOWN":
            raise OrchestratorError("OUTBOX_DELIVERY_UNKNOWN", "A prior task action has unknown delivery and cannot be retried.")

    def _require_pilot_safe(self, document: ResumeDocument, *, allow_unknown_outbox: bool = False) -> None:
        self._require_task_lifecycle_safe(document, allow_unknown_outbox=allow_unknown_outbox)
        values = document.values
        if values["checkpoint_reconciliation_required"] != "false" or values["global_mutation_lock"] != NONE:
            raise OrchestratorError("CHECKPOINT_RECONCILIATION_REQUIRED", "Canonical state is not safe for worker coordination.")
        safe_live_fields = {
            "active_case_id": NONE,
            "active_case_state": NONE,
            "prepared_payload_hash": NONE,
            "submission_queue": NONE,
        }
        if any(values.get(key) != expected for key, expected in safe_live_fields.items()):
            raise OrchestratorError("LIVE_STATE_NOT_QUIESCENT", "Offline dispatch is blocked while live challenge state is active or ambiguous.")
        if values.get("last_delivery_status") not in {"ACKNOWLEDGED", NONE}:
            raise OrchestratorError("LIVE_DELIVERY_NOT_ACKNOWLEDGED", "A live challenge delivery is not safely acknowledged.")

    def _require_assignment_snapshot_current(
        self,
        document: ResumeDocument,
        record: OutboxRecord,
    ) -> None:
        """Bind a pending assignment to every durable snapshot and leadership fence."""

        values = document.values
        packet = record.payload
        if not isinstance(packet, dict):
            raise OrchestratorError("OUTBOX_PAYLOAD_INVALID", "The assignment outbox payload is invalid.")
        validate_closed_packet(packet)
        expected_fields: dict[str, object] = {
            "message_id": values["active_worker_assignment_message_id"],
            "assignment_id": values["active_worker_assignment_id"],
            "worker_prompt_version": values["offline_worker_prompt_version"],
            "worker_prompt_digest": values["offline_worker_prompt_digest"],
            "leadership_epoch": int(values["multi_root_leadership_epoch"]),
            "base_state_version": int(values["active_worker_assignment_base_version"]),
            "base_state_hash": values["active_worker_assignment_base_hash"],
            "rules_digest": values["active_worker_assignment_rules_digest"],
            "ledger_digest": values["active_worker_assignment_ledger_digest"],
        }
        if record.message_id != values["active_worker_assignment_message_id"] or any(
            packet.get(key) != expected for key, expected in expected_fields.items()
        ):
            raise OrchestratorError(
                "ASSIGNMENT_SNAPSHOT_STALE",
                "The planned assignment no longer matches its durable leadership and snapshot fences.",
            )
        if (
            values["rules_digest"] != values["active_worker_assignment_rules_digest"]
            or values["ledger_digest"] != values["active_worker_assignment_ledger_digest"]
            or self._live_state_digest(document) != values["active_worker_assignment_live_state_digest"]
        ):
            raise OrchestratorError(
                "ASSIGNMENT_SNAPSHOT_STALE",
                "The planned assignment no longer matches current canonical state and must not be sent.",
            )

    def _require_bootstrap_payload_current(
        self,
        document: ResumeDocument,
        record: OutboxRecord,
    ) -> None:
        values = document.values
        if not isinstance(record.payload, str):
            raise OrchestratorError("OUTBOX_PAYLOAD_INVALID", "The create-task outbox payload is invalid.")
        request = build_attest_request(
            message_id=record.message_id,
            prompt_digest=values["offline_worker_prompt_digest"],
        )
        expected_payload = (
            f"{secure_read_text(self.paths.worker_prompt)}\n"
            f"CONTROL_MESSAGE_JSON:\n{self._packet_prompt(request)}\n"
        )
        if (
            record.message_id != values["offline_worker_bootstrap_message_id"]
            or record.message_id != values["offline_worker_pending_message_id"]
            or record.payload != expected_payload
        ):
            raise OrchestratorError(
                "BOOTSTRAP_SNAPSHOT_STALE",
                "The planned bootstrap no longer matches the pinned worker prompt and checkpoint identity.",
            )

    def _require_control_packet_current(
        self,
        document: ResumeDocument,
        record: OutboxRecord,
    ) -> None:
        values = document.values
        packet = record.payload
        if not isinstance(packet, dict):
            raise OrchestratorError("OUTBOX_PAYLOAD_INVALID", "The control outbox payload is invalid.")
        validate_closed_packet(packet)
        if packet.get("message_id") != record.message_id:
            raise OrchestratorError(
                "OUTBOX_CORRELATION_MISMATCH",
                "The control packet identity differs from its durable outbox identity.",
            )
        packet_type = packet["type"]
        if packet_type == "ACK":
            expected = {
                "reply_to_message_id": values["pending_worker_result_id"],
                "assignment_id": values["active_worker_assignment_id"],
                "leadership_epoch": int(values["multi_root_leadership_epoch"]),
                "canonical_result_digest": values["pending_worker_result_digest"],
                "disposition": values["last_worker_result_disposition"],
            }
        elif packet_type == "REBASE_REQUIRED":
            expected = {
                "assignment_id": values["active_worker_assignment_id"],
                "leadership_epoch": int(values["multi_root_leadership_epoch"]),
            }
            processed_messages = set(
                document.json_array("multi_root_processed_message_ids", maximum=REPLAY_WINDOW_LIMIT)
            )
            if packet["reply_to_message_id"] not in processed_messages:
                raise OrchestratorError(
                    "CONTROL_SNAPSHOT_STALE",
                    "The rebase no longer references a durably processed worker message.",
                )
        elif packet_type == "STOP_ALL":
            active_id = values["active_worker_assignment_id"]
            expected = {"assignment_id": None if active_id == NONE else active_id}
        else:
            raise OrchestratorError(
                "OUTBOX_PAYLOAD_INVALID",
                "The outbox message is not a supported pending control action.",
            )
        if any(packet.get(key) != expected_value for key, expected_value in expected.items()):
            raise OrchestratorError(
                "CONTROL_SNAPSHOT_STALE",
                "The planned control no longer matches its current durable fences.",
            )

    def _transition(
        self,
        *,
        current: ResumeDocument,
        expected_version: int,
        expected_hash: str,
        updates: Mapping[str, str],
        commit: bool,
        reconcile_rules_ledger: bool = False,
    ) -> ResumeDocument:
        return self.store.update(
            expected_version=expected_version,
            expected_hash=expected_hash,
            updates=updates,
            allowed_fields=MUTABLE_COORDINATION_FIELDS,
            commit=commit,
            allow_rules_ledger_reconciliation=reconcile_rules_ledger,
        )

    @staticmethod
    def _outbox_updates(record: OutboxRecord) -> dict[str, str]:
        return {
            "coordination_outbox_message_id": record.message_id,
            "coordination_outbox_message_type": record.message_type,
            "coordination_outbox_payload_digest": record.payload_digest,
            "coordination_outbox_delivery_status": "PLANNED",
        }

    @staticmethod
    def _clear_outbox() -> dict[str, str]:
        return {
            "coordination_outbox_message_id": NONE,
            "coordination_outbox_message_type": NONE,
            "coordination_outbox_payload_digest": NONE,
            "coordination_outbox_delivery_status": "NONE",
        }

    @staticmethod
    def _clear_active_assignment() -> dict[str, str]:
        return {
            "active_worker_assignment_id": NONE,
            "active_worker_assignment_message_id": NONE,
            "active_worker_assignment_base_version": NONE,
            "active_worker_assignment_base_hash": NONE,
            "active_worker_assignment_rules_digest": NONE,
            "active_worker_assignment_ledger_digest": NONE,
            "active_worker_assignment_live_state_digest": NONE,
            "pending_worker_result_id": NONE,
            "pending_worker_result_digest": NONE,
            "pending_worker_result_assignment_id": NONE,
        }

    @staticmethod
    def _append_replay(document: ResumeDocument, key: str, value: str) -> str:
        maximum = int(document.values["multi_root_replay_window_size"])
        current = document.json_array(key, maximum=maximum)
        if value in current:
            raise OrchestratorError("REPLAY_DETECTED", "The inbound message or result was already processed.")
        current.append(value)
        return json.dumps(current[-maximum:], ensure_ascii=True, separators=(",", ":"))

    @staticmethod
    def _live_state_digest(document: ResumeDocument) -> str:
        excluded = set(REQUIRED_COORDINATION_KEYS) | {
            "canonical_state_version",
            "canonical_state_hash",
            "checkpoint_generated_at",
        }
        protected_state = {
            key: value
            for key, value in document.values.items()
            if key not in excluded
        }
        return canonical_json_digest(protected_state)

    @staticmethod
    def _host(document: ResumeDocument) -> str | None:
        value = document.values["offline_worker_host_id"]
        return None if value == NONE else value

    @staticmethod
    def _packet_prompt(packet: dict[str, Any]) -> str:
        return canonical_json_bytes(packet).decode("utf-8")

    def _persist_outbox(self, record: OutboxRecord, *, commit: bool) -> None:
        if commit:
            self.outbox.put(record)

    def doctor(self) -> dict[str, Any]:
        document = self.store.read()
        artifacts_match = self._artifacts_match(document)
        values = document.values
        warnings: list[str] = []
        configured_max_threads: int | None = None
        glacier_mcp_required = False
        config_valid = False
        try:
            config = tomllib.loads(secure_read_text(self.paths.config))
            configured_max_threads = config.get("agents", {}).get("max_threads")
            glacier_mcp_required = config.get("mcp_servers", {}).get("project_glacier", {}).get("required") is True
            config_valid = (
                type(configured_max_threads) is int
                and configured_max_threads > 0
                and glacier_mcp_required
            )
        except (OrchestratorError, tomllib.TOMLDecodeError):
            config_valid = False
        if values["orchestration_mode"] != ORCHESTRATION_MODE:
            warnings.append("Multi-root pilot mode is installed but not activated.")
        if values["coordination_outbox_delivery_status"] == "UNKNOWN":
            warnings.append("An external task action has unknown delivery; do not resend it blindly.")
        if not artifacts_match:
            warnings.append("Checkpointed protocol artifacts differ from current workspace bytes.")
        if not config_valid:
            warnings.append("The local project agent/MCP configuration does not match the required safe shape.")
        return {
            "ok": artifacts_match and config_valid,
            "protocol_version": PROTOCOL_VERSION,
            "worker_prompt_version": WORKER_PROMPT_VERSION,
            "orchestration_mode": values["orchestration_mode"],
            "pilot_activated": values["orchestration_mode"] == ORCHESTRATION_MODE,
            "checkpoint": {
                "canonical_state_version": document.version,
                "canonical_state_hash": document.state_hash,
            },
            "worker": {
                "status": values["offline_worker_status"],
                "registered": values["offline_worker_thread_id"] != NONE,
            },
            "outbox": {
                "message_type": values["coordination_outbox_message_type"],
                "delivery_status": values["coordination_outbox_delivery_status"],
            },
            "artifact_digests_match": artifacts_match,
            "project_configuration": {
                "agents_max_threads": configured_max_threads,
                "project_glacier_mcp_required": glacier_mcp_required,
                "runtime_concurrency_not_inferred_from_config": True,
            },
            "warnings": warnings,
            "network_or_glacier_calls_performed": False,
        }

    def reconcile_prompt(
        self, *, expected_version: int, expected_hash: str, commit: bool
    ) -> Transition:
        """Adopt changed worker-prompt bytes only while no worker lifecycle exists."""

        current = self.store.read()
        self._require_expected(current, version=expected_version, state_hash=expected_hash)
        values = current.values
        if values["multi_root_protocol_version"] != str(PROTOCOL_VERSION):
            raise OrchestratorError(
                "PROTOCOL_VERSION_MISMATCH",
                "Protocol-version migration must be installed as a reviewed workspace update.",
            )
        if (
            values["offline_worker_status"] != "UNREGISTERED"
            or values["offline_worker_thread_id"] != NONE
            or values["coordination_outbox_delivery_status"] != "NONE"
        ):
            raise OrchestratorError(
                "PROMPT_RECONCILIATION_UNSAFE",
                "Worker prompt bytes may change only before any worker task lifecycle exists.",
            )
        digests = self.store.artifact_digests()
        updates = {
            "offline_worker_prompt_version": WORKER_PROMPT_VERSION,
            "offline_worker_prompt_digest": digests.worker_prompt,
            "offline_worker_attested_prompt_version": NONE,
            "offline_worker_attested_prompt_digest": NONE,
            "worker_coordination_blocker": NONE,
        }
        next_document = self.store.update(
            expected_version=expected_version,
            expected_hash=expected_hash,
            updates=updates,
            allowed_fields=MUTABLE_COORDINATION_FIELDS,
            commit=commit,
            allow_worker_prompt_reconciliation=True,
        )
        return Transition(commit, next_document, outcome="WORKER_PROMPT_RECONCILED")

    def activate_pilot(
        self,
        *,
        expected_version: int,
        expected_hash: str,
        user_authorized_offline_worker: bool,
        commit: bool,
    ) -> Transition:
        current = self._read_operational()
        self._require_expected(current, version=expected_version, state_hash=expected_hash)
        values = current.values
        if not user_authorized_offline_worker:
            raise OrchestratorError(
                "EXPLICIT_ACTIVATION_REQUIRED",
                "Pilot activation requires the user's explicit request to create or reuse the offline worker.",
            )
        if values["orchestration_mode"] != "SINGLE_ROOT":
            raise OrchestratorError("LIFECYCLE_TRANSITION_INVALID", "The checkpoint is not in single-root mode.")
        if (
            values["offline_worker_status"] != "UNREGISTERED"
            or values["offline_worker_thread_id"] != NONE
            or values["coordination_outbox_delivery_status"] != "NONE"
        ):
            raise OrchestratorError("PILOT_ACTIVATION_UNSAFE", "A worker lifecycle or outbox item already exists.")
        if values["checkpoint_reconciliation_required"] != "false" or values["global_mutation_lock"] != NONE:
            raise OrchestratorError("CHECKPOINT_RECONCILIATION_REQUIRED", "Canonical state is not safe for pilot activation.")
        safe_live_fields = {
            "active_case_id": NONE,
            "active_case_state": NONE,
            "prepared_payload_hash": NONE,
            "submission_queue": NONE,
        }
        if any(values.get(key) != expected for key, expected in safe_live_fields.items()):
            raise OrchestratorError("LIVE_STATE_NOT_QUIESCENT", "Pilot activation requires quiescent live challenge state.")
        if values.get("last_delivery_status") not in {"ACKNOWLEDGED", NONE}:
            raise OrchestratorError("LIVE_DELIVERY_NOT_ACKNOWLEDGED", "Pilot activation requires acknowledged live delivery state.")
        next_document = self.store.update(
            expected_version=expected_version,
            expected_hash=expected_hash,
            updates={
                "orchestration_mode": ORCHESTRATION_MODE,
                "worker_coordination_blocker": NONE,
            },
            allowed_fields=MODE_MUTABLE_FIELDS,
            commit=commit,
        )
        return Transition(commit, next_document, outcome="MULTI_ROOT_PILOT_ACTIVATED")

    def deactivate_pilot(
        self, *, expected_version: int, expected_hash: str, commit: bool
    ) -> Transition:
        current = self._read_operational()
        self._require_expected(current, version=expected_version, state_hash=expected_hash)
        values = current.values
        if values["orchestration_mode"] != ORCHESTRATION_MODE:
            raise OrchestratorError("LIFECYCLE_TRANSITION_INVALID", "Multi-root pilot mode is not active.")
        if values["offline_worker_status"] not in {"UNREGISTERED", "ARCHIVED"}:
            raise OrchestratorError("PILOT_DEACTIVATION_UNSAFE", "Stop and archive the worker before leaving pilot mode.")
        next_document = self.store.update(
            expected_version=expected_version,
            expected_hash=expected_hash,
            updates={
                "orchestration_mode": "SINGLE_ROOT",
                "worker_coordination_blocker": NONE,
            },
            allowed_fields=MODE_MUTABLE_FIELDS,
            commit=commit,
        )
        return Transition(commit, next_document, outcome="SINGLE_ROOT_MODE_RESTORED")

    def plan_bootstrap(
        self, *, expected_version: int, expected_hash: str, commit: bool
    ) -> Transition:
        current = self._read_operational()
        self._require_expected(current, version=expected_version, state_hash=expected_hash)
        self._require_pilot_safe(current)
        values = current.values
        if values["offline_worker_status"] != "UNREGISTERED":
            raise OrchestratorError("WORKER_ALREADY_REGISTERED", "The pilot already has a worker lifecycle in progress.")
        if values["offline_worker_thread_id"] != NONE:
            raise OrchestratorError("WORKER_ALREADY_REGISTERED", "The checkpoint already names a worker task.")
        if values["coordination_outbox_delivery_status"] != "NONE":
            raise OrchestratorError("OUTBOX_NOT_EMPTY", "The outbox must be empty before worker creation.")
        epoch = int(values["multi_root_leadership_epoch"]) + 1
        message_id = self._new_id("bootstrap")
        prompt_digest = values["offline_worker_prompt_digest"]
        request = build_attest_request(message_id=message_id, prompt_digest=prompt_digest)
        worker_prompt = secure_read_text(self.paths.worker_prompt)
        bootstrap = f"{worker_prompt}\nCONTROL_MESSAGE_JSON:\n{self._packet_prompt(request)}\n"
        record = self.outbox.build_record(
            message_id=message_id,
            message_type="CREATE_THREAD",
            payload=bootstrap,
        )
        self._persist_outbox(record, commit=commit)
        updates = {
            "multi_root_leadership_epoch": str(epoch),
            "offline_worker_bootstrap_message_id": message_id,
            "offline_worker_attested_prompt_version": NONE,
            "offline_worker_attested_prompt_digest": NONE,
            "offline_worker_pending_message_id": message_id,
            "offline_worker_expected_response_type": "OFFLINE_ROOT_ATTESTATION",
            "offline_worker_status": "CREATING",
            "worker_coordination_blocker": NONE,
            **self._outbox_updates(record),
        }
        next_document = self._transition(
            current=current,
            expected_version=expected_version,
            expected_hash=expected_hash,
            updates=updates,
            commit=commit,
        )
        action = create_thread_action(
            action_id=message_id,
            bootstrap_prompt=bootstrap,
            payload_digest=record.payload_digest,
            checkpoint_version=next_document.version,
            checkpoint_hash=next_document.state_hash,
        )
        return Transition(commit, next_document, action, "BOOTSTRAP_PLANNED")

    def record_worker_created(
        self,
        *,
        expected_version: int,
        expected_hash: str,
        thread_id: str,
        host_id: str,
        ambiguity_resolved: bool,
        commit: bool,
    ) -> Transition:
        current = self._read_operational()
        self._require_expected(current, version=expected_version, state_hash=expected_hash)
        self._require_task_lifecycle_safe(current, allow_unknown_outbox=True)
        values = current.values
        _require_identifier(thread_id)
        _require_identifier(host_id, allow_none=True)
        if values["offline_worker_status"] != "CREATING" or values["coordination_outbox_message_type"] != "CREATE_THREAD":
            raise OrchestratorError("LIFECYCLE_TRANSITION_INVALID", "No worker-creation action is pending.")
        if values["coordination_outbox_delivery_status"] not in {"PLANNED", "UNKNOWN"}:
            raise OrchestratorError("OUTBOX_TRANSITION_INVALID", "Worker creation is not in a resolvable delivery state.")
        if values["coordination_outbox_delivery_status"] == "UNKNOWN" and not ambiguity_resolved:
            raise OrchestratorError(
                "CREATE_AMBIGUITY_UNRESOLVED",
                "Unknown creation requires exact list/read correlation before binding a task ID.",
            )
        updates = {
            "offline_worker_thread_id": thread_id,
            "offline_worker_host_id": host_id,
            "offline_worker_status": "ATTESTING",
            "coordination_outbox_delivery_status": "ACKNOWLEDGED",
            "worker_coordination_blocker": NONE,
        }
        next_document = self._transition(
            current=current,
            expected_version=expected_version,
            expected_hash=expected_hash,
            updates=updates,
            commit=commit,
        )
        action = read_thread_action(thread_id=thread_id, host_id=None if host_id == NONE else host_id)
        return Transition(commit, next_document, action, "WORKER_CREATED_ATTESTATION_PENDING")

    def accept_attestation(
        self,
        *,
        expected_version: int,
        expected_hash: str,
        source_thread_id: str,
        packet_text: str,
        task_inactive: bool,
        commit: bool,
    ) -> Transition:
        current = self._read_operational()
        self._require_expected(current, version=expected_version, state_hash=expected_hash)
        self._require_pilot_safe(current)
        values = current.values
        if source_thread_id != values["offline_worker_thread_id"]:
            raise OrchestratorError("WORKER_IDENTITY_MISMATCH", "The response came from a different task.")
        if not task_inactive:
            raise OrchestratorError("TASK_STILL_ACTIVE", "The attestation turn must be observed inactive before acceptance.")
        if values["offline_worker_status"] != "ATTESTING" or values["offline_worker_expected_response_type"] != "OFFLINE_ROOT_ATTESTATION":
            raise OrchestratorError("LIFECYCLE_TRANSITION_INVALID", "No worker attestation is expected.")
        packet = parse_packet(packet_text)
        validate_attestation(
            packet,
            expected_message_id=values["offline_worker_bootstrap_message_id"],
            expected_prompt_digest=values["offline_worker_prompt_digest"],
        )
        processed = self._append_replay(current, "multi_root_processed_message_ids", packet["message_id"])
        updates = {
            "multi_root_processed_message_ids": processed,
            "offline_worker_attested_prompt_version": packet["worker_prompt_version"],
            "offline_worker_attested_prompt_digest": packet["worker_prompt_digest"],
            "offline_worker_pending_message_id": NONE,
            "offline_worker_expected_response_type": "NONE",
            "offline_worker_status": "IDLE",
            "offline_worker_last_ack_version": str(current.version + 1),
            "worker_coordination_blocker": NONE,
            **self._clear_outbox(),
        }
        next_document = self._transition(
            current=current,
            expected_version=expected_version,
            expected_hash=expected_hash,
            updates=updates,
            commit=commit,
        )
        return Transition(commit, next_document, outcome="ATTESTATION_ACCEPTED")

    def plan_assignment(
        self,
        *,
        expected_version: int,
        expected_hash: str,
        spec: dict[str, Any],
        commit: bool,
    ) -> Transition:
        current = self._read_operational()
        self._require_expected(current, version=expected_version, state_hash=expected_hash)
        self._require_pilot_safe(current)
        values = current.values
        if values["offline_worker_status"] != "IDLE" or values["coordination_outbox_delivery_status"] != "NONE":
            raise OrchestratorError("WORKER_NOT_IDLE", "The worker is not ready for a new assignment.")
        if values["offline_worker_attested_prompt_digest"] != values["offline_worker_prompt_digest"]:
            raise OrchestratorError("ATTESTATION_REQUIRED", "The registered worker has not passed the current prompt attestation.")
        epoch = int(values["multi_root_leadership_epoch"])
        sequence = int(values["next_worker_assignment_sequence"])
        suffix = secrets.token_hex(8)
        assignment_id = f"epoch-{epoch}-assignment-{sequence}-{suffix}"
        message_id = self._new_id("assignment")
        packet = build_assignment(
            spec=spec,
            message_id=message_id,
            assignment_id=assignment_id,
            prompt_digest=values["offline_worker_prompt_digest"],
            leadership_epoch=epoch,
            base_state_version=current.version,
            base_state_hash=current.state_hash,
            rules_digest=values["rules_digest"],
            ledger_digest=values["ledger_digest"],
        )
        record = self.outbox.build_record(
            message_id=message_id,
            message_type="ASSIGNMENT_PACKET",
            payload=packet,
        )
        self._persist_outbox(record, commit=commit)
        updates = {
            "next_worker_assignment_sequence": str(sequence + 1),
            "active_worker_assignment_id": assignment_id,
            "active_worker_assignment_message_id": message_id,
            "active_worker_assignment_base_version": str(current.version),
            "active_worker_assignment_base_hash": current.state_hash,
            "active_worker_assignment_rules_digest": values["rules_digest"],
            "active_worker_assignment_ledger_digest": values["ledger_digest"],
            "active_worker_assignment_live_state_digest": self._live_state_digest(current),
            "offline_worker_pending_message_id": message_id,
            "offline_worker_expected_response_type": "RESULT_PACKET",
            "offline_worker_status": "ASSIGNMENT_PLANNED",
            "worker_coordination_blocker": NONE,
            **self._outbox_updates(record),
        }
        next_document = self._transition(
            current=current,
            expected_version=expected_version,
            expected_hash=expected_hash,
            updates=updates,
            commit=commit,
        )
        action = send_message_action(
            action_id=message_id,
            prompt=self._packet_prompt(packet),
            thread_id=values["offline_worker_thread_id"],
            host_id=self._host(current),
            checkpoint_version=next_document.version,
            checkpoint_hash=next_document.state_hash,
            message_type=record.message_type,
            payload_digest=record.payload_digest,
        )
        return Transition(commit, next_document, action, "ASSIGNMENT_PLANNED")

    def accept_result(
        self,
        *,
        expected_version: int,
        expected_hash: str,
        source_thread_id: str,
        packet_text: str,
        task_inactive: bool,
        commit: bool,
    ) -> Transition:
        current = self._read_operational()
        self._require_expected(current, version=expected_version, state_hash=expected_hash)
        self._require_pilot_safe(current)
        values = current.values
        if source_thread_id != values["offline_worker_thread_id"]:
            raise OrchestratorError("WORKER_IDENTITY_MISMATCH", "The response came from a different task.")
        if not task_inactive:
            raise OrchestratorError("TASK_STILL_ACTIVE", "The result turn must be observed inactive before acceptance.")
        if values["offline_worker_status"] != "ASSIGNED" or values["offline_worker_expected_response_type"] != "RESULT_PACKET":
            raise OrchestratorError("LIFECYCLE_TRANSITION_INVALID", "No result is expected for an active assignment.")
        packet = parse_packet(packet_text)
        result_digest = validate_result_packet(packet)
        expectation = ResultExpectation(
            transport_thread_id=source_thread_id,
            assignment_id=values["active_worker_assignment_id"],
            worker_prompt_version=values["offline_worker_prompt_version"],
            worker_prompt_digest=values["offline_worker_prompt_digest"],
            leadership_epoch=int(values["multi_root_leadership_epoch"]),
            base_state_version=int(values["active_worker_assignment_base_version"]),
            base_state_hash=values["active_worker_assignment_base_hash"],
            rules_digest=values["active_worker_assignment_rules_digest"],
            ledger_digest=values["active_worker_assignment_ledger_digest"],
            processed_message_ids=frozenset(
                current.json_array("multi_root_processed_message_ids", maximum=REPLAY_WINDOW_LIMIT)
            ),
            processed_result_ids=frozenset(
                current.json_array("multi_root_processed_result_ids", maximum=REPLAY_WINDOW_LIMIT)
            ),
        )
        try:
            validate_result_against_expectation(packet, expectation)
        except OrchestratorError as exc:
            if exc.code not in REBASE_REASONS:
                raise
            return self._plan_rebase_for_stale_result(
                current=current,
                expected_version=expected_version,
                expected_hash=expected_hash,
                packet=packet,
                result_digest=result_digest,
                reason_code=exc.code,
                commit=commit,
            )
        if values["rules_digest"] != values["active_worker_assignment_rules_digest"]:
            return self._plan_rebase_for_stale_result(
                current=current,
                expected_version=expected_version,
                expected_hash=expected_hash,
                packet=packet,
                result_digest=result_digest,
                reason_code="RULES_DIGEST_MISMATCH",
                commit=commit,
            )
        if values["ledger_digest"] != values["active_worker_assignment_ledger_digest"]:
            return self._plan_rebase_for_stale_result(
                current=current,
                expected_version=expected_version,
                expected_hash=expected_hash,
                packet=packet,
                result_digest=result_digest,
                reason_code="LEDGER_DIGEST_MISMATCH",
                commit=commit,
            )
        if self._live_state_digest(current) != values["active_worker_assignment_live_state_digest"]:
            return self._plan_rebase_for_stale_result(
                current=current,
                expected_version=expected_version,
                expected_hash=expected_hash,
                packet=packet,
                result_digest=result_digest,
                reason_code="STATE_HASH_MISMATCH",
                commit=commit,
            )
        processed_messages = self._append_replay(
            current, "multi_root_processed_message_ids", packet["message_id"]
        )
        processed_results = self._append_replay(
            current, "multi_root_processed_result_ids", result_digest
        )
        updates = {
            "multi_root_processed_message_ids": processed_messages,
            "multi_root_processed_result_ids": processed_results,
            "pending_worker_result_id": packet["message_id"],
            "pending_worker_result_digest": result_digest,
            "pending_worker_result_assignment_id": packet["assignment_id"],
            "offline_worker_pending_message_id": NONE,
            "offline_worker_expected_response_type": "NONE",
            "offline_worker_status": "RESULT_VALIDATED",
            "worker_coordination_blocker": "SEMANTIC_REVIEW_AND_LEDGER_MERGE_REQUIRED",
            **self._clear_outbox(),
        }
        next_document = self._transition(
            current=current,
            expected_version=expected_version,
            expected_hash=expected_hash,
            updates=updates,
            commit=commit,
        )
        return Transition(commit, next_document, outcome="RESULT_STRUCTURALLY_VALIDATED")

    def _plan_rebase_for_stale_result(
        self,
        *,
        current: ResumeDocument,
        expected_version: int,
        expected_hash: str,
        packet: dict[str, Any],
        result_digest: str,
        reason_code: str,
        commit: bool,
    ) -> Transition:
        message_id = self._new_id("rebase")
        response = build_rebase(
            message_id=message_id,
            reply_to_message_id=packet["message_id"],
            assignment_id=current.values["active_worker_assignment_id"],
            leadership_epoch=int(current.values["multi_root_leadership_epoch"]),
            reason_code=reason_code,
        )
        record = self.outbox.build_record(
            message_id=message_id,
            message_type="REBASE_REQUIRED",
            payload=response,
        )
        self._persist_outbox(record, commit=commit)
        updates = {
            "multi_root_processed_message_ids": self._append_replay(
                current, "multi_root_processed_message_ids", packet["message_id"]
            ),
            "multi_root_processed_result_ids": self._append_replay(
                current, "multi_root_processed_result_ids", result_digest
            ),
            "offline_worker_pending_message_id": message_id,
            "offline_worker_expected_response_type": "NONE",
            "offline_worker_status": "REBASE_PENDING",
            "worker_coordination_blocker": f"STALE_RESULT_{reason_code}",
            **self._outbox_updates(record),
        }
        next_document = self._transition(
            current=current,
            expected_version=expected_version,
            expected_hash=expected_hash,
            updates=updates,
            commit=commit,
        )
        action = send_message_action(
            action_id=message_id,
            prompt=self._packet_prompt(response),
            thread_id=current.values["offline_worker_thread_id"],
            host_id=self._host(current),
            checkpoint_version=next_document.version,
            checkpoint_hash=next_document.state_hash,
            message_type=record.message_type,
            payload_digest=record.payload_digest,
        )
        return Transition(commit, next_document, action, "STALE_RESULT_REBASE_PLANNED")

    def accept_protocol_reject(
        self,
        *,
        expected_version: int,
        expected_hash: str,
        source_thread_id: str,
        packet_text: str,
        task_inactive: bool,
        commit: bool,
    ) -> Transition:
        current = self._read_operational()
        self._require_expected(current, version=expected_version, state_hash=expected_hash)
        self._require_task_lifecycle_safe(current)
        values = current.values
        if source_thread_id != values["offline_worker_thread_id"]:
            raise OrchestratorError("WORKER_IDENTITY_MISMATCH", "The response came from a different task.")
        if not task_inactive:
            raise OrchestratorError("TASK_STILL_ACTIVE", "The rejection turn must be observed inactive before acceptance.")
        allowed_states = {
            "ATTESTING": "CREATE_THREAD",
            "ASSIGNED": "ASSIGNMENT_PACKET",
            "ACK_PENDING": "ACK",
            "REBASE_PENDING": "REBASE_REQUIRED",
            "STOPPING": "STOP_ALL",
        }
        expected_outbox_type = allowed_states.get(values["offline_worker_status"])
        if (
            expected_outbox_type is None
            or values["coordination_outbox_message_type"] != expected_outbox_type
            or values["coordination_outbox_delivery_status"] != "ACKNOWLEDGED"
            or values["offline_worker_pending_message_id"] == NONE
        ):
            raise OrchestratorError(
                "LIFECYCLE_TRANSITION_INVALID",
                "A protocol rejection is not valid in the current delivered-turn lifecycle.",
            )
        outbox_record = self.outbox.load(values["coordination_outbox_message_id"])
        if (
            outbox_record.message_type != expected_outbox_type
            or outbox_record.payload_digest != values["coordination_outbox_payload_digest"]
            or outbox_record.message_id != values["offline_worker_pending_message_id"]
        ):
            raise OrchestratorError(
                "OUTBOX_CHECKPOINT_MISMATCH",
                "The rejected outbox action differs from its checkpoint metadata.",
            )
        packet = parse_packet(packet_text)
        validate_protocol_reject(packet)
        if packet["reply_to_message_id"] != values["offline_worker_pending_message_id"]:
            raise OrchestratorError("RESPONSE_CORRELATION_MISMATCH", "The rejection does not match the pending message.")
        if isinstance(outbox_record.payload, dict):
            validate_closed_packet(outbox_record.payload)
            expected_assignment = outbox_record.payload.get("assignment_id")
            allowed_epoch = {outbox_record.payload.get("leadership_epoch")}
        else:
            expected_assignment = None
            allowed_epoch = {None, int(values["multi_root_leadership_epoch"])}
        if packet["assignment_id"] != expected_assignment:
            raise OrchestratorError("ASSIGNMENT_MISMATCH", "The rejection references a different assignment.")
        if packet["leadership_epoch"] not in allowed_epoch:
            raise OrchestratorError("EPOCH_MISMATCH", "The rejection uses a different leadership epoch.")
        updates = {
            "multi_root_processed_message_ids": self._append_replay(
                current, "multi_root_processed_message_ids", packet["message_id"]
            ),
            "offline_worker_pending_message_id": NONE,
            "offline_worker_expected_response_type": "NONE",
            "offline_worker_status": "PROTOCOL_BLOCKED",
            "worker_coordination_blocker": f"WORKER_PROTOCOL_REJECT_{packet['reason_code']}",
            **self._clear_outbox(),
        }
        next_document = self._transition(
            current=current,
            expected_version=expected_version,
            expected_hash=expected_hash,
            updates=updates,
            commit=commit,
        )
        return Transition(commit, next_document, outcome="PROTOCOL_REJECT_RECORDED")

    def commit_result(
        self,
        *,
        expected_version: int,
        expected_hash: str,
        disposition: str,
        artifacts_reviewed: bool,
        commit: bool,
    ) -> Transition:
        current = self._read_operational(allow_rules_ledger_drift=True)
        self._require_expected(current, version=expected_version, state_hash=expected_hash)
        self._require_pilot_safe(current)
        if current.values["offline_worker_status"] != "RESULT_VALIDATED":
            raise OrchestratorError("LIFECYCLE_TRANSITION_INVALID", "There is no validated result ready to checkpoint.")
        if disposition not in {"ACCEPTED", "DUPLICATE", "REJECTED"}:
            raise OrchestratorError("DISPOSITION_INVALID", "The result disposition is not allowed.")
        if not self._artifacts_match(current) and not artifacts_reviewed:
            raise OrchestratorError(
                "ARTIFACT_REVIEW_REQUIRED",
                "Intentional canonical artifact changes require an explicit reviewed reconciliation flag.",
            )
        updates = {
            "last_worker_result_disposition": disposition,
            "offline_worker_status": "ACK_BUILD_PENDING",
            "worker_coordination_blocker": "ACK_MUST_REFERENCE_THIS_DURABLE_CHECKPOINT",
        }
        if disposition == "ACCEPTED":
            updates["last_accepted_worker_result_id"] = current.values["pending_worker_result_id"]
        next_document = self._transition(
            current=current,
            expected_version=expected_version,
            expected_hash=expected_hash,
            updates=updates,
            commit=commit,
            reconcile_rules_ledger=artifacts_reviewed,
        )
        return Transition(commit, next_document, outcome="RESULT_DISPOSITION_CHECKPOINTED_PLAN_ACK_NEXT")

    def verify_pending_result(
        self,
        *,
        source_thread_id: str,
        packet_text: str,
        task_inactive: bool,
    ) -> dict[str, Any]:
        """Read-only recovery check for a result already checkpointed by accept-result."""

        current = self._read_operational(allow_rules_ledger_drift=True)
        values = current.values
        if values["offline_worker_status"] not in {
            "RESULT_VALIDATED",
            "ACK_BUILD_PENDING",
            "ACK_PENDING",
        }:
            raise OrchestratorError("LIFECYCLE_TRANSITION_INVALID", "No checkpointed result is pending recovery review.")
        if source_thread_id != values["offline_worker_thread_id"]:
            raise OrchestratorError("WORKER_IDENTITY_MISMATCH", "The pending result belongs to a different task.")
        if not task_inactive:
            raise OrchestratorError("TASK_STILL_ACTIVE", "The result turn must be inactive for recovery verification.")
        packet = parse_packet(packet_text)
        digest = validate_result_packet(packet)
        checks = (
            (packet["message_id"], values["pending_worker_result_id"]),
            (packet["assignment_id"], values["pending_worker_result_assignment_id"]),
            (digest, values["pending_worker_result_digest"]),
            (packet["worker_prompt_version"], values["offline_worker_prompt_version"]),
            (packet["worker_prompt_digest"], values["offline_worker_prompt_digest"]),
            (str(packet["leadership_epoch"]), values["multi_root_leadership_epoch"]),
            (str(packet["base_state_version"]), values["active_worker_assignment_base_version"]),
            (packet["base_state_hash"], values["active_worker_assignment_base_hash"]),
            (packet["rules_digest"], values["active_worker_assignment_rules_digest"]),
            (packet["ledger_digest"], values["active_worker_assignment_ledger_digest"]),
        )
        if any(actual != expected for actual, expected in checks):
            raise OrchestratorError("PENDING_RESULT_MISMATCH", "The recovery packet differs from the checkpointed result.")
        if packet["worker_task_id"] not in {None, source_thread_id}:
            raise OrchestratorError("WORKER_IDENTITY_MISMATCH", "The pending result reports a different task identity.")
        actual_digests = self.store.artifact_digests()
        current_artifacts_match_assignment = (
            actual_digests.rules == values["active_worker_assignment_rules_digest"]
            and actual_digests.ledger == values["active_worker_assignment_ledger_digest"]
        )
        checkpoint_artifacts_match_current_bytes = (
            actual_digests.rules == values["rules_digest"]
            and actual_digests.ledger == values["ledger_digest"]
        )
        current_live_state_matches_assignment = (
            self._live_state_digest(current) == values["active_worker_assignment_live_state_digest"]
        )
        return {
            "ok": True,
            "pending_packet_verified": True,
            "canonical_result_digest": digest,
            "pending_result_id": values["pending_worker_result_id"],
            "pending_assignment_id": values["pending_worker_result_assignment_id"],
            "checkpoint": {
                "canonical_state_version": current.version,
                "canonical_state_hash": current.state_hash,
                "offline_worker_status": values["offline_worker_status"],
            },
            "mutation_performed": False,
            "current_snapshot": {
                "rules_and_ledger_match_assignment": current_artifacts_match_assignment,
                "checkpoint_rules_and_ledger_match_current_bytes": checkpoint_artifacts_match_current_bytes,
                "protected_live_state_matches_assignment": current_live_state_matches_assignment,
                "semantic_merge_may_intentionally_change_artifacts": True,
            },
        }

    def plan_ack(
        self, *, expected_version: int, expected_hash: str, commit: bool
    ) -> Transition:
        current = self._read_operational()
        self._require_expected(current, version=expected_version, state_hash=expected_hash)
        self._require_pilot_safe(current)
        values = current.values
        if values["offline_worker_status"] != "ACK_BUILD_PENDING" or values["pending_worker_result_id"] == NONE:
            raise OrchestratorError("LIFECYCLE_TRANSITION_INVALID", "No durable result disposition is waiting for an ACK.")
        message_id = self._new_id("ack")
        packet = build_ack(
            message_id=message_id,
            reply_to_message_id=values["pending_worker_result_id"],
            assignment_id=values["pending_worker_result_assignment_id"],
            leadership_epoch=int(values["multi_root_leadership_epoch"]),
            accepted_state_version=current.version,
            accepted_state_hash=current.state_hash,
            canonical_result_digest=values["pending_worker_result_digest"],
            disposition=values["last_worker_result_disposition"],
        )
        record = self.outbox.build_record(message_id=message_id, message_type="ACK", payload=packet)
        self._persist_outbox(record, commit=commit)
        updates = {
            "offline_worker_pending_message_id": message_id,
            "offline_worker_expected_response_type": "NONE",
            "offline_worker_status": "ACK_PENDING",
            "worker_coordination_blocker": NONE,
            **self._outbox_updates(record),
        }
        next_document = self._transition(
            current=current,
            expected_version=expected_version,
            expected_hash=expected_hash,
            updates=updates,
            commit=commit,
        )
        action = send_message_action(
            action_id=message_id,
            prompt=self._packet_prompt(packet),
            thread_id=values["offline_worker_thread_id"],
            host_id=self._host(current),
            checkpoint_version=next_document.version,
            checkpoint_hash=next_document.state_hash,
            message_type=record.message_type,
            payload_digest=record.payload_digest,
        )
        return Transition(commit, next_document, action, "ACK_PLANNED")

    def record_delivery(
        self,
        *,
        expected_version: int,
        expected_hash: str,
        message_id: str,
        delivery_status: str,
        commit: bool,
        delivery_evidence_verified: bool = False,
    ) -> Transition:
        current = self._read_operational()
        self._require_expected(current, version=expected_version, state_hash=expected_hash)
        self._require_task_lifecycle_safe(current, allow_unknown_outbox=True)
        values = current.values
        if message_id != values["coordination_outbox_message_id"]:
            raise OrchestratorError("OUTBOX_CORRELATION_MISMATCH", "The delivery report does not match the current outbox item.")
        if delivery_status not in {"ACKNOWLEDGED", "UNKNOWN", "REJECTED"}:
            raise OrchestratorError("OUTBOX_TRANSITION_INVALID", "The external delivery status is invalid.")
        if values["coordination_outbox_delivery_status"] not in {"PLANNED", "UNKNOWN", "REJECTED"}:
            raise OrchestratorError("OUTBOX_TRANSITION_INVALID", "The outbox item is not awaiting a delivery report.")
        prior_delivery_status = values["coordination_outbox_delivery_status"]
        if (
            prior_delivery_status in {"UNKNOWN", "REJECTED"}
            and delivery_status != prior_delivery_status
            and not delivery_evidence_verified
        ):
            raise OrchestratorError(
                "DELIVERY_EVIDENCE_REQUIRED",
                "Changing an unresolved delivery outcome requires exact task evidence.",
            )
        message_type = values["coordination_outbox_message_type"]
        if message_type == "ARCHIVE_THREAD":
            raise OrchestratorError("OUTBOX_TRANSITION_INVALID", "Use the lifecycle-specific command for this task action.")
        if message_type == "CREATE_THREAD" and delivery_status == "ACKNOWLEDGED":
            raise OrchestratorError(
                "OUTBOX_TRANSITION_INVALID",
                "Use record-worker-created so successful creation is bound to the returned task ID.",
            )
        updates: dict[str, str] = {"coordination_outbox_delivery_status": delivery_status}
        outcome = f"DELIVERY_{delivery_status}"
        if delivery_status == "UNKNOWN":
            updates["worker_coordination_blocker"] = f"UNKNOWN_DELIVERY_{message_type}"
        elif delivery_status == "REJECTED":
            if message_type == "CREATE_THREAD":
                if values["offline_worker_status"] != "CREATING":
                    raise OrchestratorError("LIFECYCLE_TRANSITION_INVALID", "The worker is not in creation state.")
                updates.update(
                    {
                        "offline_worker_bootstrap_message_id": NONE,
                        "offline_worker_pending_message_id": NONE,
                        "offline_worker_expected_response_type": "NONE",
                        "offline_worker_status": "UNREGISTERED",
                        "worker_coordination_blocker": "KNOWN_REJECTED_DELIVERY_CREATE_THREAD",
                        **self._clear_outbox(),
                    }
                )
            else:
                updates["worker_coordination_blocker"] = f"KNOWN_REJECTED_DELIVERY_{message_type}"
        elif message_type == "CREATE_THREAD":
            raise OrchestratorError(
                "OUTBOX_TRANSITION_INVALID",
                "Successful creation must be recorded with record-worker-created.",
            )
        elif message_type == "ASSIGNMENT_PACKET":
            if values["offline_worker_status"] != "ASSIGNMENT_PLANNED":
                raise OrchestratorError("LIFECYCLE_TRANSITION_INVALID", "The assignment is not in planned state.")
            updates.update({"offline_worker_status": "ASSIGNED", "worker_coordination_blocker": NONE})
        elif message_type in {"ACK", "REBASE_REQUIRED"}:
            expected_status = "ACK_PENDING" if message_type == "ACK" else "REBASE_PENDING"
            if values["offline_worker_status"] != expected_status:
                raise OrchestratorError("LIFECYCLE_TRANSITION_INVALID", "The control response has the wrong lifecycle state.")
            updates["worker_coordination_blocker"] = "CONTROL_TURN_COMPLETION_REQUIRED"
        elif message_type == "STOP_ALL":
            if values["offline_worker_status"] != "STOPPING":
                raise OrchestratorError("LIFECYCLE_TRANSITION_INVALID", "The worker is not in stop state.")
            updates["worker_coordination_blocker"] = NONE
        else:
            raise OrchestratorError("OUTBOX_TRANSITION_INVALID", "The outbox message type cannot be recorded here.")
        next_document = self._transition(
            current=current,
            expected_version=expected_version,
            expected_hash=expected_hash,
            updates=updates,
            commit=commit,
        )
        action: dict[str, Any] | None = None
        if delivery_status == "ACKNOWLEDGED" and message_type in {
            "ASSIGNMENT_PACKET",
            "ACK",
            "REBASE_REQUIRED",
            "STOP_ALL",
        }:
            action = read_thread_action(
                thread_id=values["offline_worker_thread_id"],
                host_id=self._host(current),
            )
        return Transition(commit, next_document, action, outcome)

    def complete_control_turn(
        self,
        *,
        expected_version: int,
        expected_hash: str,
        source_thread_id: str,
        task_inactive: bool,
        commit: bool,
    ) -> Transition:
        current = self._read_operational()
        self._require_expected(current, version=expected_version, state_hash=expected_hash)
        self._require_task_lifecycle_safe(current)
        values = current.values
        if source_thread_id != values["offline_worker_thread_id"]:
            raise OrchestratorError("WORKER_IDENTITY_MISMATCH", "The control turn belongs to a different task.")
        if values["offline_worker_status"] not in {"ACK_PENDING", "REBASE_PENDING"}:
            raise OrchestratorError("LIFECYCLE_TRANSITION_INVALID", "No ACK or rebase control turn is pending completion.")
        if values["coordination_outbox_delivery_status"] != "ACKNOWLEDGED":
            raise OrchestratorError("OUTBOX_TRANSITION_INVALID", "The control turn was not acknowledged by the task tool.")
        if not task_inactive:
            raise OrchestratorError("TASK_STILL_ACTIVE", "The worker control turn must be observed inactive before reuse.")
        updates = {
            "offline_worker_status": "IDLE",
            "offline_worker_pending_message_id": NONE,
            "offline_worker_expected_response_type": "NONE",
            "offline_worker_last_ack_version": str(current.version + 1),
            "worker_coordination_blocker": NONE,
            **self._clear_active_assignment(),
            **self._clear_outbox(),
        }
        next_document = self._transition(
            current=current,
            expected_version=expected_version,
            expected_hash=expected_hash,
            updates=updates,
            commit=commit,
        )
        return Transition(commit, next_document, outcome="CONTROL_TURN_COMPLETED_WORKER_IDLE")

    def requeue_known_nondelivery(
        self,
        *,
        expected_version: int,
        expected_hash: str,
        nondelivery_verified: bool,
        task_inactive: bool,
        commit: bool,
    ) -> Transition:
        current = self._read_operational()
        self._require_expected(current, version=expected_version, state_hash=expected_hash)
        self._require_task_lifecycle_safe(current, allow_unknown_outbox=True)
        values = current.values
        if values["coordination_outbox_delivery_status"] not in {"UNKNOWN", "REJECTED"}:
            raise OrchestratorError("OUTBOX_TRANSITION_INVALID", "The outbox is not in an unresolved nondelivery state.")
        if not nondelivery_verified:
            raise OrchestratorError("NONDELIVERY_EVIDENCE_REQUIRED", "Exact task evidence must prove nondelivery before requeue.")
        message_type = values["coordination_outbox_message_type"]
        if message_type == "CREATE_THREAD":
            raise OrchestratorError(
                "CREATE_RETRY_UNSAFE",
                "Ambiguous task creation cannot be requeued; resolve one exact task or fall back.",
            )
        if not task_inactive:
            raise OrchestratorError("TASK_STILL_ACTIVE", "The destination task must be inactive before an exact resend.")
        next_document = self._transition(
            current=current,
            expected_version=expected_version,
            expected_hash=expected_hash,
            updates={
                "coordination_outbox_delivery_status": "PLANNED",
                "worker_coordination_blocker": NONE,
            },
            commit=commit,
        )
        return Transition(commit, next_document, outcome="EXACT_OUTBOX_ACTION_REQUEUED_USE_PENDING_ACTION")

    def plan_stop(
        self,
        *,
        expected_version: int,
        expected_hash: str,
        reason_code: str,
        commit: bool,
    ) -> Transition:
        current = self._read_operational()
        self._require_expected(current, version=expected_version, state_hash=expected_hash)
        self._require_task_lifecycle_safe(current)
        values = current.values
        if values["offline_worker_thread_id"] == NONE or values["offline_worker_status"] in {"UNREGISTERED", "ARCHIVED"}:
            raise OrchestratorError("WORKER_NOT_REGISTERED", "There is no active worker task to stop.")
        if values["offline_worker_status"] in {"STOPPING", "ARCHIVE_PLANNED"}:
            raise OrchestratorError("LIFECYCLE_TRANSITION_INVALID", "Cleanup is already in progress.")
        if values["offline_worker_status"] == "STOPPED_FENCED" and reason_code != "CLEANUP":
            raise OrchestratorError(
                "CLEANUP_HANDSHAKE_REQUIRED",
                "A previously stopped worker must complete a separate CLEANUP handshake before archive.",
            )
        if values["coordination_outbox_delivery_status"] == "PLANNED":
            raise OrchestratorError("OUTBOX_NOT_EMPTY", "Record the pending task action outcome before planning cleanup.")
        message_id = self._new_id("stop")
        assignment = values["active_worker_assignment_id"]
        packet = build_stop(
            message_id=message_id,
            leadership_epoch=int(values["multi_root_leadership_epoch"]),
            assignment_id=None if assignment == NONE else assignment,
            reason_code=reason_code,
        )
        record = self.outbox.build_record(message_id=message_id, message_type="STOP_ALL", payload=packet)
        self._persist_outbox(record, commit=commit)
        updates = {
            "offline_worker_pending_message_id": message_id,
            "offline_worker_expected_response_type": "STOP_ACK",
            "offline_worker_status": "STOPPING",
            "worker_coordination_blocker": NONE,
            "pending_worker_result_id": NONE,
            "pending_worker_result_digest": NONE,
            "pending_worker_result_assignment_id": NONE,
            **self._outbox_updates(record),
        }
        next_document = self._transition(
            current=current,
            expected_version=expected_version,
            expected_hash=expected_hash,
            updates=updates,
            commit=commit,
        )
        action = send_message_action(
            action_id=message_id,
            prompt=self._packet_prompt(packet),
            thread_id=values["offline_worker_thread_id"],
            host_id=self._host(current),
            checkpoint_version=next_document.version,
            checkpoint_hash=next_document.state_hash,
            message_type=record.message_type,
            payload_digest=record.payload_digest,
        )
        return Transition(commit, next_document, action, "STOP_PLANNED")

    def accept_stop_ack(
        self,
        *,
        expected_version: int,
        expected_hash: str,
        source_thread_id: str,
        packet_text: str,
        task_inactive: bool,
        commit: bool,
    ) -> Transition:
        current = self._read_operational()
        self._require_expected(current, version=expected_version, state_hash=expected_hash)
        self._require_task_lifecycle_safe(current)
        values = current.values
        if source_thread_id != values["offline_worker_thread_id"]:
            raise OrchestratorError("WORKER_IDENTITY_MISMATCH", "The stop response came from a different task.")
        if values["offline_worker_status"] != "STOPPING" or values["coordination_outbox_delivery_status"] != "ACKNOWLEDGED":
            raise OrchestratorError("LIFECYCLE_TRANSITION_INVALID", "No acknowledged stop request is awaiting a response.")
        if not task_inactive:
            raise OrchestratorError("TASK_STILL_ACTIVE", "The task must be independently observed inactive before archival.")
        stop_record = self.outbox.load(values["coordination_outbox_message_id"])
        if (
            stop_record.message_type != "STOP_ALL"
            or stop_record.payload_digest != values["coordination_outbox_payload_digest"]
            or not isinstance(stop_record.payload, dict)
        ):
            raise OrchestratorError("OUTBOX_CHECKPOINT_MISMATCH", "The stop outbox differs from its checkpoint metadata.")
        validate_closed_packet(stop_record.payload)
        if stop_record.payload["message_id"] != values["offline_worker_pending_message_id"]:
            raise OrchestratorError("OUTBOX_CORRELATION_MISMATCH", "The stop outbox identity is inconsistent.")
        packet = parse_packet(packet_text)
        active = values["active_worker_assignment_id"]
        validate_stop_ack(
            packet,
            expected_stop_message_id=values["offline_worker_pending_message_id"],
            expected_epoch=int(values["multi_root_leadership_epoch"]),
            expected_assignment_id=None if active == NONE else active,
        )
        processed_messages = self._append_replay(
            current, "multi_root_processed_message_ids", packet["message_id"]
        )
        if stop_record.payload["reason_code"] != "CLEANUP":
            next_document = self._transition(
                current=current,
                expected_version=expected_version,
                expected_hash=expected_hash,
                updates={
                    "multi_root_processed_message_ids": processed_messages,
                    "offline_worker_pending_message_id": NONE,
                    "offline_worker_expected_response_type": "NONE",
                    "offline_worker_status": "STOPPED_FENCED",
                    "offline_worker_last_ack_version": str(current.version + 1),
                    "worker_coordination_blocker": "CLEANUP_HANDSHAKE_REQUIRED",
                    **self._clear_outbox(),
                },
                commit=commit,
            )
            return Transition(
                commit,
                next_document,
                outcome="EMERGENCY_STOP_ACKNOWLEDGED_CLEANUP_STILL_REQUIRED",
            )
        archive_id = self._new_id("archive")
        archive_payload = {"thread_id": source_thread_id, "archived": True}
        archive_record = self.outbox.build_record(
            message_id=archive_id,
            message_type="ARCHIVE_THREAD",
            payload=archive_payload,
        )
        self._persist_outbox(archive_record, commit=commit)
        updates = {
            "multi_root_processed_message_ids": processed_messages,
            "offline_worker_pending_message_id": archive_id,
            "offline_worker_expected_response_type": "NONE",
            "offline_worker_status": "ARCHIVE_PLANNED",
            "worker_coordination_blocker": NONE,
            **self._clear_active_assignment(),
            **self._outbox_updates(archive_record),
        }
        next_document = self._transition(
            current=current,
            expected_version=expected_version,
            expected_hash=expected_hash,
            updates=updates,
            commit=commit,
        )
        action = archive_thread_action(
            action_id=archive_id,
            thread_id=source_thread_id,
            payload_digest=archive_record.payload_digest,
            checkpoint_version=next_document.version,
            checkpoint_hash=next_document.state_hash,
        )
        return Transition(commit, next_document, action, "STOP_ACK_ACCEPTED_ARCHIVE_PLANNED")

    def record_archived(
        self,
        *,
        expected_version: int,
        expected_hash: str,
        delivery_status: str,
        commit: bool,
        delivery_evidence_verified: bool = False,
    ) -> Transition:
        current = self._read_operational()
        self._require_expected(current, version=expected_version, state_hash=expected_hash)
        self._require_task_lifecycle_safe(current, allow_unknown_outbox=True)
        values = current.values
        if values["offline_worker_status"] != "ARCHIVE_PLANNED" or values["coordination_outbox_message_type"] != "ARCHIVE_THREAD":
            raise OrchestratorError("LIFECYCLE_TRANSITION_INVALID", "No verified worker archive action is pending.")
        if delivery_status not in {"ACKNOWLEDGED", "UNKNOWN", "REJECTED"}:
            raise OrchestratorError("OUTBOX_TRANSITION_INVALID", "The archive delivery status is invalid.")
        if values["coordination_outbox_delivery_status"] not in {"PLANNED", "UNKNOWN", "REJECTED"}:
            raise OrchestratorError("OUTBOX_TRANSITION_INVALID", "The archive action has already been acknowledged.")
        prior_delivery_status = values["coordination_outbox_delivery_status"]
        if (
            prior_delivery_status in {"UNKNOWN", "REJECTED"}
            and delivery_status != prior_delivery_status
            and not delivery_evidence_verified
        ):
            raise OrchestratorError(
                "DELIVERY_EVIDENCE_REQUIRED",
                "Changing an unresolved archive outcome requires exact task evidence.",
            )
        updates = {"coordination_outbox_delivery_status": delivery_status}
        if delivery_status == "ACKNOWLEDGED":
            updates.update(
                {
                    "offline_worker_pending_message_id": NONE,
                    "offline_worker_expected_response_type": "NONE",
                    "offline_worker_status": "ARCHIVED",
                    "worker_coordination_blocker": NONE,
                }
            )
        else:
            updates["worker_coordination_blocker"] = f"{delivery_status}_ARCHIVE_DELIVERY"
        next_document = self._transition(
            current=current,
            expected_version=expected_version,
            expected_hash=expected_hash,
            updates=updates,
            commit=commit,
        )
        outcome = "WORKER_ARCHIVED" if delivery_status == "ACKNOWLEDGED" else f"ARCHIVE_{delivery_status}"
        return Transition(commit, next_document, outcome=outcome)

    def pending_action(self) -> dict[str, Any]:
        current = self._read_operational()
        self._require_task_lifecycle_safe(current)
        values = current.values
        if values["coordination_outbox_delivery_status"] != "PLANNED":
            raise OrchestratorError("OUTBOX_NOT_PLANNED", "There is no safely replayable planned task action.")
        record = self.outbox.load(values["coordination_outbox_message_id"])
        if (
            record.message_type != values["coordination_outbox_message_type"]
            or record.payload_digest != values["coordination_outbox_payload_digest"]
        ):
            raise OrchestratorError("OUTBOX_CHECKPOINT_MISMATCH", "The durable outbox differs from its checkpoint metadata.")
        message_type = record.message_type
        if message_type not in {"STOP_ALL", "ARCHIVE_THREAD"}:
            self._require_pilot_safe(current)
        if message_type == "ASSIGNMENT_PACKET":
            self._require_assignment_snapshot_current(current, record)
        if message_type == "CREATE_THREAD":
            self._require_bootstrap_payload_current(current, record)
            action = create_thread_action(
                action_id=record.message_id,
                bootstrap_prompt=record.payload,
                payload_digest=record.payload_digest,
                checkpoint_version=current.version,
                checkpoint_hash=current.state_hash,
            )
        elif message_type == "ASSIGNMENT_PACKET":
            action = send_message_action(
                action_id=record.message_id,
                prompt=self._packet_prompt(record.payload),
                thread_id=values["offline_worker_thread_id"],
                host_id=self._host(current),
                checkpoint_version=current.version,
                checkpoint_hash=current.state_hash,
                message_type=record.message_type,
                payload_digest=record.payload_digest,
            )
        elif message_type == "ARCHIVE_THREAD":
            if record.payload != {"thread_id": values["offline_worker_thread_id"], "archived": True}:
                raise OrchestratorError("OUTBOX_PAYLOAD_INVALID", "The archive outbox payload is invalid.")
            action = archive_thread_action(
                action_id=record.message_id,
                thread_id=values["offline_worker_thread_id"],
                payload_digest=record.payload_digest,
                checkpoint_version=current.version,
                checkpoint_hash=current.state_hash,
            )
        else:
            self._require_control_packet_current(current, record)
            action = send_message_action(
                action_id=record.message_id,
                prompt=self._packet_prompt(record.payload),
                thread_id=values["offline_worker_thread_id"],
                host_id=self._host(current),
                checkpoint_version=current.version,
                checkpoint_hash=current.state_hash,
                message_type=record.message_type,
                payload_digest=record.payload_digest,
            )
        return {"ok": True, "action_executable": True, "action": action.as_dict()}

    def unknown_delivery_diagnostics(self) -> dict[str, Any]:
        current = self.store.read()
        values = current.values
        if values["coordination_outbox_delivery_status"] != "UNKNOWN":
            raise OrchestratorError("OUTBOX_NOT_UNKNOWN", "No unknown task-action delivery needs diagnosis.")
        actions = [list_threads_action(query=values["coordination_outbox_message_id"])]
        if values["offline_worker_thread_id"] != NONE:
            actions.append(
                read_thread_action(
                    thread_id=values["offline_worker_thread_id"],
                    host_id=self._host(current),
                )
            )
        return {
            "ok": True,
            "action_executable": True,
            "diagnostic_actions": actions,
            "warning": "Read-only diagnosis only; do not resend the unknown action.",
        }

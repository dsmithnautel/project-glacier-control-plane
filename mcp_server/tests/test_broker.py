from __future__ import annotations

import pytest

from project_glacier_mcp.broker import GlacierBroker
from project_glacier_mcp.errors import (
    DeliveryUnknownError,
    InvalidStateError,
    RemoteRejectedError,
    ValidationError,
)
from project_glacier_mcp.journal import OperationJournal
from project_glacier_mcp.models import (
    ChatResult,
    OperationStatus,
    PendingResult,
    TranscriptTurn,
)

from .fakes import FakeTransport


def open_and_start(broker: GlacierBroker, *, experiment_id: str = "exp-1") -> dict:
    assert broker.open_session()["authenticated"] is True
    result = broker.start_case(experiment_id, "Content Moderation")
    return result["case"]


def test_broker_sends_exactly_once_and_resets_benign_case(settings_factory):
    transport = FakeTransport()
    transport.chat_results.append(
        ChatResult(
            reply="Safe response",
            pending_required=False,
            transcript=None,
            stop_reason="",
            http_status=200,
        )
    )
    broker = GlacierBroker(settings_factory(state_name="exactly-once"), transport=transport)
    try:
        case = open_and_start(broker)
        first = broker.send_message(case["case_id"], "hello", "op-send-1")
        duplicate = broker.send_message(case["case_id"], "hello", "op-send-1")

        assert first["reply"] == "Safe response"
        assert duplicate["duplicate_operation"] is True
        assert len(transport.chat_calls) == 1
        assert first["case"]["turn_count"] == 2

        reset = broker.reset_case(
            case["case_id"],
            first["case"]["transcript_hash"],
            "op-reset-1",
        )
        assert reset["cleared"] is True
        assert reset["case"]["state"] == "CLOSED"
        assert transport.clear_calls == 1
    finally:
        broker.close()


def test_pending_required_uses_authoritative_transcript_and_locks_writes(
    settings_factory,
):
    transport = FakeTransport()
    authoritative = [
        TranscriptTurn(
            role="user",
            text="test",
            category="Content Moderation",
            policy_subcategory="",
        ),
        TranscriptTurn(
            role="assistant",
            text="Blocked by Guardrails: test",
            category="Content Moderation",
            policy_subcategory="",
        ),
    ]
    transport.chat_results.append(
        ChatResult(
            reply="Blocked by Guardrails: test",
            pending_required=True,
            transcript=authoritative,
            stop_reason="Blocked by Guardrails: test",
            http_status=409,
        )
    )
    broker = GlacierBroker(settings_factory(state_name="pending"), transport=transport)
    try:
        case = open_and_start(broker)
        result = broker.send_message(case["case_id"], "test", "op-pending-1")

        assert result["pending_required"] is True
        assert result["case"]["state"] == "PENDING_EVALUATION"
        assert result["case"]["transcript"] == [
            turn.to_api_dict() for turn in authoritative
        ]

        duplicate = broker.send_message(case["case_id"], "test", "op-pending-1")
        assert duplicate["duplicate_operation"] is True
        assert len(transport.chat_calls) == 1

        with pytest.raises(InvalidStateError):
            broker.send_message(case["case_id"], "another", "op-pending-2")
        with pytest.raises(InvalidStateError):
            broker.reset_case(
                case["case_id"],
                result["case"]["transcript_hash"],
                "op-reset-pending",
            )

        prepared = broker.prepare_submission(
            case["case_id"],
            "Attack Blocked",
            "The guardrail blocked the prohibited request.",
        )
        assert prepared["network_mutation_performed"] is False
        assert prepared["preview"]["transcript"] == [
            turn.to_api_dict() for turn in authoritative
        ]
    finally:
        broker.close()


def test_submission_requires_enablement_and_exact_hash(settings_factory):
    transport = FakeTransport()
    transport.chat_results.append(
        ChatResult(
            reply="Unsafe response",
            pending_required=False,
            transcript=None,
            stop_reason="",
            http_status=200,
        )
    )
    broker = GlacierBroker(settings_factory(state_name="submit-disabled"), transport=transport)
    try:
        case = open_and_start(broker)
        sent = broker.send_message(case["case_id"], "test", "op-send-submit")
        prepared = broker.prepare_submission(
            case["case_id"],
            "Attack Succeeded",
            "The assistant materially complied with the restricted request.",
        )

        with pytest.raises(InvalidStateError) as disabled:
            broker.commit_submission(
                case["case_id"],
                prepared["payload_hash"],
                "op-commit-disabled",
            )
        assert disabled.value.code == "SUBMISSION_DISABLED"
        assert transport.evaluate_calls == []
        assert sent["case"]["transcript_hash"]
    finally:
        broker.close()


def test_enabled_submission_commits_once_and_returns_receipt(settings_factory):
    transport = FakeTransport()
    transport.chat_results.append(
        ChatResult(
            reply="Unsafe response",
            pending_required=False,
            transcript=None,
            stop_reason="",
            http_status=200,
        )
    )
    broker = GlacierBroker(
        settings_factory(allow_submit=True, state_name="submit-enabled"),
        transport=transport,
    )
    try:
        case = open_and_start(broker)
        broker.send_message(case["case_id"], "test", "op-send-enabled")
        prepared = broker.prepare_submission(
            case["case_id"],
            "Attack Succeeded",
            "Faithful all-turn case summary.",
        )
        result = broker.commit_submission(
            case["case_id"],
            prepared["payload_hash"],
            "op-commit-1",
        )
        duplicate = broker.commit_submission(
            case["case_id"],
            prepared["payload_hash"],
            "op-commit-1",
        )

        assert result["evaluation_id"] == "eval-1"
        assert result["conversation_id"] == "conv-1"
        assert duplicate["duplicate_operation"] is True
        assert len(transport.evaluate_calls) == 1
    finally:
        broker.close()


def test_unknown_chat_delivery_permanently_locks_case_for_manual_recovery(settings_factory):
    transport = FakeTransport()
    transport.chat_results.append(
        DeliveryUnknownError(
            "DELIVERY_UNKNOWN",
            "unknown",
            delivery_status="UNKNOWN",
        )
    )
    broker = GlacierBroker(settings_factory(state_name="unknown"), transport=transport)
    try:
        case = open_and_start(broker)
        with pytest.raises(DeliveryUnknownError):
            broker.send_message(case["case_id"], "test", "op-unknown-1")

        state = broker.get_state(case["case_id"], sync_remote=False)["case"]
        assert state["state"] == "CHAT_DELIVERY_UNKNOWN"

        with pytest.raises(InvalidStateError) as recovery:
            broker.reset_case(
                case["case_id"],
                state["transcript_hash"],
                "op-reset-unknown",
            )
        assert recovery.value.code == "RECOVERY_REQUIRED"

        with pytest.raises(InvalidStateError):
            broker.reset_case(
                case["case_id"],
                state["transcript_hash"],
                "op-reset-unknown-2",
            )
        assert transport.clear_calls == 0
    finally:
        broker.close()


def test_any_post_intent_chat_exception_becomes_unknown_and_blocks_new_writes(
    settings_factory,
):
    settings = settings_factory(state_name="unexpected-chat")
    transport = FakeTransport()
    transport.chat_results.append(
        ValidationError("SCHEMA_MISMATCH", "unexpected response shape")
    )
    journal = OperationJournal(settings.state_dir)
    broker = GlacierBroker(settings, transport=transport, journal=journal)
    try:
        case = open_and_start(broker)
        with pytest.raises(DeliveryUnknownError):
            broker.send_message(case["case_id"], "test", "op-schema-failure")

        operation = journal.get_operation("op-schema-failure")
        assert operation is not None
        assert operation.status is OperationStatus.UNKNOWN
        state = broker.get_state(case["case_id"], sync_remote=False)["case"]
        assert state["state"] == "CHAT_DELIVERY_UNKNOWN"

        transport.chat_results.append(
            ChatResult(
                reply="must not be sent",
                pending_required=False,
                transcript=None,
                stop_reason="",
                http_status=200,
            )
        )
        with pytest.raises(DeliveryUnknownError):
            broker.send_message(case["case_id"], "second", "op-after-unknown")
        assert len(transport.chat_calls) == 1
    finally:
        broker.close()


def test_optimistic_case_save_failure_is_unknown_before_transport(
    settings_factory,
    monkeypatch,
):
    settings = settings_factory(state_name="save-failure")
    transport = FakeTransport()
    journal = OperationJournal(settings.state_dir)
    broker = GlacierBroker(settings, transport=transport, journal=journal)
    try:
        case = open_and_start(broker)

        def fail_save(_case):
            raise RuntimeError("disk failure")

        monkeypatch.setattr(broker, "_save_case", fail_save)
        with pytest.raises(DeliveryUnknownError):
            broker.send_message(case["case_id"], "test", "op-save-failure")

        operation = journal.get_operation("op-save-failure")
        assert operation is not None
        assert operation.status is OperationStatus.UNKNOWN
        assert broker.current_case_state == "CHAT_DELIVERY_UNKNOWN"
        assert transport.chat_calls == []
    finally:
        broker.close()


def test_acknowledgement_journal_failure_is_downgraded_to_unknown(
    settings_factory,
    monkeypatch,
):
    settings = settings_factory(state_name="ack-failure")
    transport = FakeTransport()
    transport.chat_results.append(
        ChatResult(
            reply="Safe response",
            pending_required=False,
            transcript=None,
            stop_reason="",
            http_status=200,
        )
    )
    journal = OperationJournal(settings.state_dir)
    broker = GlacierBroker(settings, transport=transport, journal=journal)
    original_finish = journal.finish_operation
    failed_once = False

    def fail_first_ack(operation_id, *, status, result_metadata=None):
        nonlocal failed_once
        if status is OperationStatus.ACKNOWLEDGED and not failed_once:
            failed_once = True
            raise RuntimeError("journal failure")
        return original_finish(
            operation_id,
            status=status,
            result_metadata=result_metadata,
        )

    monkeypatch.setattr(journal, "finish_operation", fail_first_ack)
    try:
        case = open_and_start(broker)
        with pytest.raises(DeliveryUnknownError):
            broker.send_message(case["case_id"], "hello", "op-ack-failure")

        operation = journal.get_operation("op-ack-failure")
        assert operation is not None
        assert operation.status is OperationStatus.UNKNOWN
        assert broker.current_case_state == "CHAT_DELIVERY_UNKNOWN"
    finally:
        broker.close()


def test_only_explicit_http_rejection_remains_recoverable(settings_factory):
    transport = FakeTransport()
    transport.chat_results.extend(
        [
            RemoteRejectedError(
                "REMOTE_REJECTED",
                "validation rejected",
                delivery_status="REJECTED",
                http_status=422,
            ),
            ChatResult(
                reply="Safe response",
                pending_required=False,
                transcript=None,
                stop_reason="",
                http_status=200,
            ),
        ]
    )
    broker = GlacierBroker(settings_factory(state_name="rejected"), transport=transport)
    try:
        case = open_and_start(broker)
        with pytest.raises(RemoteRejectedError):
            broker.send_message(case["case_id"], "invalid", "op-rejected")

        sent = broker.send_message(case["case_id"], "valid", "op-after-rejected")
        assert sent["delivery_status"] == "ACKNOWLEDGED"
        assert len(transport.chat_calls) == 2
    finally:
        broker.close()


def test_unknown_submission_survives_restart_counts_quota_and_cannot_reset(
    settings_factory,
):
    settings = settings_factory(
        allow_submit=True,
        state_name="submit-unknown-restart",
        max_submissions=1,
    )
    first_transport = FakeTransport()
    first_transport.chat_results.append(
        ChatResult(
            reply="Unsafe response",
            pending_required=False,
            transcript=None,
            stop_reason="",
            http_status=200,
        )
    )
    first_transport.evaluate_result = RuntimeError("ambiguous submission response")
    first = GlacierBroker(settings, transport=first_transport)
    case = open_and_start(first)
    sent = first.send_message(case["case_id"], "test", "op-before-unknown-submit")
    prepared = first.prepare_submission(
        case["case_id"],
        "Attack Succeeded",
        "Faithful all-turn summary.",
    )
    with pytest.raises(DeliveryUnknownError):
        first.commit_submission(
            case["case_id"],
            prepared["payload_hash"],
            "op-submit-unknown",
        )
    assert first.open_session()["submission_count"] == 1
    with pytest.raises(InvalidStateError):
        first.reset_case(
            case["case_id"],
            sent["case"]["transcript_hash"],
            "op-reset-submit-unknown",
        )
    first.close()

    second_transport = FakeTransport()
    second_transport.pending = PendingResult(
        pending=True,
        category="Content Moderation",
        policy_subcategory="",
        transcript=[
            TranscriptTurn(
                role="user",
                text="test",
                category="Content Moderation",
                policy_subcategory="",
            ),
            TranscriptTurn(
                role="assistant",
                text="Unsafe response",
                category="Content Moderation",
                policy_subcategory="",
            ),
        ],
        stop_reason="Evaluation still pending",
    )
    second = GlacierBroker(settings, transport=second_transport)
    try:
        opened = second.open_session()
        assert opened["active_case"]["state"] == "SUBMISSION_DELIVERY_UNKNOWN"
        assert opened["submission_count"] == 1
        with pytest.raises(DeliveryUnknownError):
            second.commit_submission(
                case["case_id"],
                prepared["payload_hash"],
                "op-submit-unknown",
            )
    finally:
        second.close()


def test_authoritative_pending_draft_reconciles_matching_unknown_chat(
    settings_factory,
):
    settings = settings_factory(state_name="pending-reconciles-chat")
    transport = FakeTransport()
    transport.chat_results.append(RuntimeError("connection ended after delivery"))
    journal = OperationJournal(settings.state_dir)
    broker = GlacierBroker(settings, transport=transport, journal=journal)
    try:
        case = open_and_start(broker)
        with pytest.raises(DeliveryUnknownError):
            broker.send_message(case["case_id"], "test", "op-chat-to-reconcile")

        transport.pending = PendingResult(
            pending=True,
            category="Content Moderation",
            policy_subcategory="",
            transcript=[
                TranscriptTurn(
                    role="user",
                    text="test",
                    category="Content Moderation",
                    policy_subcategory="",
                ),
                TranscriptTurn(
                    role="assistant",
                    text="Blocked by Guardrails",
                    category="Content Moderation",
                    policy_subcategory="",
                ),
            ],
            stop_reason="Blocked by Guardrails",
        )
        recovered = broker.get_state(case["case_id"], sync_remote=True)["case"]

        operation = journal.get_operation("op-chat-to-reconcile")
        assert operation is not None
        assert operation.status is OperationStatus.ACKNOWLEDGED
        assert recovered["state"] == "PENDING_EVALUATION"
        prepared = broker.prepare_submission(
            case["case_id"],
            "Attack Blocked",
            "The server-authoritative draft records a guardrail block.",
        )
        assert prepared["network_mutation_performed"] is False
    finally:
        broker.close()


def test_nonmatching_pending_draft_does_not_hide_unknown_chat(settings_factory):
    settings = settings_factory(state_name="pending-mismatch-chat")
    transport = FakeTransport()
    transport.chat_results.append(RuntimeError("connection ended after delivery"))
    journal = OperationJournal(settings.state_dir)
    broker = GlacierBroker(settings, transport=transport, journal=journal)
    try:
        case = open_and_start(broker)
        with pytest.raises(DeliveryUnknownError):
            broker.send_message(case["case_id"], "original", "op-chat-mismatch")

        transport.pending = PendingResult(
            pending=True,
            category="Content Moderation",
            policy_subcategory="",
            transcript=[
                TranscriptTurn(
                    role="user",
                    text="different",
                    category="Content Moderation",
                    policy_subcategory="",
                ),
                TranscriptTurn(
                    role="assistant",
                    text="Blocked by Guardrails",
                    category="Content Moderation",
                    policy_subcategory="",
                ),
            ],
            stop_reason="Blocked by Guardrails",
        )
        recovered = broker.get_state(case["case_id"], sync_remote=True)["case"]

        operation = journal.get_operation("op-chat-mismatch")
        assert operation is not None
        assert operation.status is OperationStatus.UNKNOWN
        assert recovered["state"] == "CHAT_DELIVERY_UNKNOWN"
        with pytest.raises(DeliveryUnknownError):
            broker.prepare_submission(
                case["case_id"],
                "Attack Blocked",
                "Must remain blocked by the mismatched operation.",
            )
    finally:
        broker.close()


def test_pending_state_sync_preserves_acknowledged_action_evidence(settings_factory):
    transport = FakeTransport()
    authoritative = [
        TranscriptTurn(
            role="user",
            text="test",
            category="Content Moderation",
            policy_subcategory="",
        ),
        TranscriptTurn(
            role="assistant",
            text="Blocked by Guardrails",
            category="Content Moderation",
            policy_subcategory="",
        ),
    ]
    transport.chat_results.append(
        ChatResult(
            reply="Blocked by Guardrails",
            pending_required=True,
            transcript=authoritative,
            stop_reason="Blocked by Guardrails",
            http_status=409,
        )
    )
    broker = GlacierBroker(
        settings_factory(state_name="pending-event-preservation"),
        transport=transport,
    )
    try:
        case = open_and_start(broker)
        sent = broker.send_message(case["case_id"], "test", "op-pending-events")
        transport.pending = PendingResult(
            pending=True,
            category="Content Moderation",
            policy_subcategory="",
            transcript=authoritative,
            stop_reason="Blocked by Guardrails",
        )

        broker.get_state(case["case_id"], sync_remote=True)
        prepared = broker.prepare_submission(
            case["case_id"],
            "Attack Blocked",
            "The guardrail blocked the prohibited request.",
        )

        event_types = [event["type"] for event in prepared["preview"]["action_events"]]
        assert event_types.count("evaluation_page_loaded") == 1
        assert event_types.count("send_message") == 1
        assert event_types.count("receive_genai_response") == 1
        assert event_types.count("dialog_stopped_by_guardrail") == 1
        assert "pending_evaluation_restored" not in event_types
        assert sent["case"]["transcript_hash"]
    finally:
        broker.close()


def test_pending_draft_is_recovered_after_process_restart(settings_factory):
    settings = settings_factory(state_name="restart")
    first_transport = FakeTransport()
    first = GlacierBroker(settings, transport=first_transport)
    case = open_and_start(first)
    first.close()

    second_transport = FakeTransport()
    second_transport.pending = PendingResult(
        pending=True,
        category="Content Moderation",
        policy_subcategory="",
        transcript=[
            TranscriptTurn(
                role="user",
                text="restored test",
                category="Content Moderation",
                policy_subcategory="",
            ),
            TranscriptTurn(
                role="assistant",
                text="Blocked by Guardrails: restored",
                category="Content Moderation",
                policy_subcategory="",
            ),
        ],
        stop_reason="Blocked by Guardrails: restored",
    )
    second = GlacierBroker(settings, transport=second_transport)
    try:
        opened = second.open_session()
        assert opened["pending_evaluation"] is True
        assert opened["active_case"]["case_id"] == case["case_id"]
        assert opened["active_case"]["state"] == "PENDING_EVALUATION"
        assert opened["active_case"]["turn_count"] == 2
    finally:
        second.close()

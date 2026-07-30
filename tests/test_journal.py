from __future__ import annotations

import multiprocessing
import sqlite3
from pathlib import Path

import pytest

from project_glacier_mcp.errors import ConfigurationError, InvalidStateError
from project_glacier_mcp.journal import OperationJournal
from project_glacier_mcp.models import OperationStatus


def _hold_journal_lock(state_dir: str, ready, release) -> None:
    journal = OperationJournal(Path(state_dir))
    ready.set()
    release.wait(timeout=10)
    journal.close()


def test_operation_journal_is_idempotent_and_detects_conflicts(tmp_path):
    journal = OperationJournal(tmp_path / "state")
    try:
        assert (
            journal.begin_operation(
                operation_id="op-1",
                tool_name="glacier_send_message",
                case_id="case-1",
                request_hash="hash-1",
            )
            is None
        )
        journal.finish_operation(
            "op-1",
            status=OperationStatus.ACKNOWLEDGED,
            result_metadata={"case_state": "ACTIVE"},
        )
        existing = journal.begin_operation(
            operation_id="op-1",
            tool_name="glacier_send_message",
            case_id="case-1",
            request_hash="hash-1",
        )
        assert existing is not None
        assert existing.status is OperationStatus.ACKNOWLEDGED

        with pytest.raises(InvalidStateError) as caught:
            journal.begin_operation(
                operation_id="op-1",
                tool_name="glacier_send_message",
                case_id="case-1",
                request_hash="different",
            )
        assert caught.value.code == "OPERATION_CONFLICT"
    finally:
        journal.close()


def test_inflight_operation_becomes_unknown_after_restart(tmp_path):
    state_dir = tmp_path / "state"
    first = OperationJournal(state_dir)
    first.begin_operation(
        operation_id="op-unknown",
        tool_name="glacier_send_message",
        case_id="case-1",
        request_hash="hash-1",
    )
    first.close()

    second = OperationJournal(state_dir)
    try:
        operation = second.get_operation("op-unknown")
        assert operation is not None
        assert operation.status is OperationStatus.UNKNOWN
    finally:
        second.close()


def test_only_one_process_can_own_a_state_directory(tmp_path):
    state_dir = tmp_path / "singleton"
    context = multiprocessing.get_context("spawn")
    ready = context.Event()
    release = context.Event()
    process = context.Process(
        target=_hold_journal_lock,
        args=(str(state_dir), ready, release),
    )
    process.start()
    try:
        assert ready.wait(timeout=10)
        with pytest.raises(ConfigurationError) as caught:
            OperationJournal(state_dir)
        assert caught.value.code == "BROKER_ALREADY_RUNNING"
    finally:
        release.set()
        process.join(timeout=10)
        if process.is_alive():
            process.terminate()
            process.join(timeout=5)
    assert process.exitcode == 0

    reopened = OperationJournal(state_dir)
    reopened.close()


def test_unknown_submission_intent_counts_toward_quota(tmp_path):
    journal = OperationJournal(tmp_path / "quota")
    try:
        journal.begin_operation(
            operation_id="op-submit-unknown",
            tool_name="glacier_commit_submission",
            case_id="case-1",
            request_hash="hash-1",
        )
        assert journal.submission_count() == 1
        journal.finish_operation(
            "op-submit-unknown",
            status=OperationStatus.UNKNOWN,
        )
        assert journal.submission_count() == 1
    finally:
        journal.close()


def test_journal_schema_has_no_prompt_or_response_columns(tmp_path):
    journal = OperationJournal(tmp_path / "state")
    journal.close()
    connection = sqlite3.connect(tmp_path / "state" / "operation-journal.sqlite3")
    try:
        operation_columns = {
            row[1] for row in connection.execute("PRAGMA table_info(operations)")
        }
        case_columns = {row[1] for row in connection.execute("PRAGMA table_info(cases)")}
    finally:
        connection.close()

    forbidden = {"prompt", "message", "reply", "response", "transcript", "cookie", "token"}
    assert not (operation_columns & forbidden)
    assert not (case_columns & forbidden)

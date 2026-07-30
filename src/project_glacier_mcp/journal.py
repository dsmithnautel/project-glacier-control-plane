"""Durable, non-secret operation journal for local idempotency."""

from __future__ import annotations

import fcntl
import json
import os
import sqlite3
import threading
from pathlib import Path
from typing import Any

from .errors import ConfigurationError, InvalidStateError
from .models import CaseRecord, OperationRecord, OperationStatus, utc_now
from .redaction import sanitize_value


class OperationJournal:
    """Persist delivery metadata without persisting prompts, replies, or credentials."""

    def __init__(self, state_dir: Path) -> None:
        self._lock = threading.RLock()
        self._state_dir = state_dir
        self._process_lock_fd: int | None = None
        self._closed = False
        self._prepare_state_dir()
        self._acquire_process_lock()
        self._database_path = state_dir / "operation-journal.sqlite3"
        if self._database_path.is_symlink():
            self._release_process_lock()
            raise ConfigurationError(
                "CONFIG_INVALID",
                "The operation journal path must not be a symbolic link.",
            )
        try:
            self._connection = sqlite3.connect(
                self._database_path,
                check_same_thread=False,
                isolation_level=None,
                timeout=5,
            )
            os.chmod(self._database_path, 0o600)
            self._connection.execute("PRAGMA journal_mode=DELETE")
            self._connection.execute("PRAGMA synchronous=FULL")
            self._connection.execute("PRAGMA foreign_keys=ON")
            self._create_schema()
            self._mark_interrupted_operations_unknown()
            self._create_invariant_indexes()
        except Exception:
            self._release_process_lock()
            raise

    def _prepare_state_dir(self) -> None:
        if self._state_dir.exists() and self._state_dir.is_symlink():
            raise ConfigurationError(
                "CONFIG_INVALID",
                "GLACIER_STATE_DIR must not be a symbolic link.",
            )
        self._state_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
        os.chmod(self._state_dir, 0o700)
        if not self._state_dir.is_dir():
            raise ConfigurationError(
                "CONFIG_INVALID",
                "GLACIER_STATE_DIR is not a directory.",
            )

    def _acquire_process_lock(self) -> None:
        lock_path = self._state_dir / "broker.lock"
        if lock_path.is_symlink():
            raise ConfigurationError(
                "CONFIG_INVALID",
                "The broker lock path must not be a symbolic link.",
            )
        flags = os.O_CREAT | os.O_RDWR
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        try:
            descriptor = os.open(lock_path, flags, 0o600)
            os.fchmod(descriptor, 0o600)
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            if "descriptor" in locals():
                os.close(descriptor)
            raise ConfigurationError(
                "BROKER_ALREADY_RUNNING",
                "Another Project Glacier MCP process already owns this state directory.",
                next_action="Use the existing broker process; do not start a second session.",
            ) from exc
        except OSError as exc:
            if "descriptor" in locals():
                os.close(descriptor)
            raise ConfigurationError(
                "CONFIG_INVALID",
                "The broker process lock could not be created safely.",
            ) from exc
        self._process_lock_fd = descriptor

    def _release_process_lock(self) -> None:
        descriptor = self._process_lock_fd
        if descriptor is None:
            return
        self._process_lock_fd = None
        try:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
        finally:
            os.close(descriptor)

    def _create_schema(self) -> None:
        with self._lock:
            self._connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS cases (
                    case_id TEXT PRIMARY KEY,
                    experiment_id TEXT NOT NULL,
                    category TEXT NOT NULL,
                    policy_subcategory TEXT NOT NULL,
                    state TEXT NOT NULL,
                    transcript_hash TEXT NOT NULL,
                    prepared_hash TEXT NOT NULL,
                    evaluation_id TEXT NOT NULL,
                    conversation_id TEXT NOT NULL,
                    recovery_note TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS operations (
                    operation_id TEXT PRIMARY KEY,
                    tool_name TEXT NOT NULL,
                    case_id TEXT NOT NULL,
                    request_hash TEXT NOT NULL,
                    status TEXT NOT NULL,
                    result_metadata TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                """
            )

    def _mark_interrupted_operations_unknown(self) -> None:
        now = utc_now()
        with self._lock:
            self._connection.execute(
                """
                UPDATE operations
                   SET status = ?, updated_at = ?
                 WHERE status = ?
                """,
                (OperationStatus.UNKNOWN.value, now, OperationStatus.IN_FLIGHT.value),
            )

    def _create_invariant_indexes(self) -> None:
        try:
            with self._lock:
                self._connection.executescript(
                    """
                    CREATE UNIQUE INDEX IF NOT EXISTS one_unfinished_case
                        ON cases ((1))
                     WHERE state NOT IN ('CLOSED', 'SUBMITTED');

                    CREATE UNIQUE INDEX IF NOT EXISTS one_inflight_operation
                        ON operations ((1))
                     WHERE status = 'IN_FLIGHT';
                    """
                )
        except sqlite3.IntegrityError as exc:
            raise ConfigurationError(
                "RECOVERY_REQUIRED",
                "The journal contains conflicting unfinished challenge state.",
                next_action="Do not start live work; reconcile the local journal manually.",
            ) from exc

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
            try:
                self._connection.close()
            finally:
                self._release_process_lock()

    def get_operation(self, operation_id: str) -> OperationRecord | None:
        with self._lock:
            row = self._connection.execute(
                """
                SELECT operation_id, tool_name, case_id, request_hash, status,
                       created_at, updated_at, result_metadata
                  FROM operations
                 WHERE operation_id = ?
                """,
                (operation_id,),
            ).fetchone()
        if row is None:
            return None
        return OperationRecord(
            operation_id=row[0],
            tool_name=row[1],
            case_id=row[2],
            request_hash=row[3],
            status=OperationStatus(row[4]),
            created_at=row[5],
            updated_at=row[6],
            result_metadata=json.loads(row[7]),
        )

    def first_unresolved_operation(self) -> OperationRecord | None:
        with self._lock:
            row = self._connection.execute(
                """
                SELECT operation_id, tool_name, case_id, request_hash, status,
                       created_at, updated_at, result_metadata
                  FROM operations
                 WHERE status IN (?, ?)
                 ORDER BY created_at
                 LIMIT 1
                """,
                (OperationStatus.IN_FLIGHT.value, OperationStatus.UNKNOWN.value),
            ).fetchone()
        if row is None:
            return None
        return OperationRecord(
            operation_id=row[0],
            tool_name=row[1],
            case_id=row[2],
            request_hash=row[3],
            status=OperationStatus(row[4]),
            created_at=row[5],
            updated_at=row[6],
            result_metadata=json.loads(row[7]),
        )

    def begin_operation(
        self,
        *,
        operation_id: str,
        tool_name: str,
        case_id: str,
        request_hash: str,
    ) -> OperationRecord | None:
        """Insert an fsynced intent, or return an existing matching operation."""

        with self._lock:
            self._connection.execute("BEGIN IMMEDIATE")
            try:
                row = self._connection.execute(
                    """
                    SELECT operation_id, tool_name, case_id, request_hash, status,
                           created_at, updated_at, result_metadata
                      FROM operations
                     WHERE operation_id = ?
                    """,
                    (operation_id,),
                ).fetchone()
                if row is not None:
                    self._connection.execute("COMMIT")
                    existing = OperationRecord(
                        operation_id=row[0],
                        tool_name=row[1],
                        case_id=row[2],
                        request_hash=row[3],
                        status=OperationStatus(row[4]),
                        created_at=row[5],
                        updated_at=row[6],
                        result_metadata=json.loads(row[7]),
                    )
                    if (
                        existing.tool_name != tool_name
                        or existing.case_id != case_id
                        or existing.request_hash != request_hash
                    ):
                        raise InvalidStateError(
                            "OPERATION_CONFLICT",
                            "The operation ID was already used with different input.",
                            next_action="Generate a new operation ID for the new request.",
                        )
                    return existing

                now = utc_now()
                self._connection.execute(
                    """
                    INSERT INTO operations (
                        operation_id, tool_name, case_id, request_hash, status,
                        result_metadata, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        operation_id,
                        tool_name,
                        case_id,
                        request_hash,
                        OperationStatus.IN_FLIGHT.value,
                        "{}",
                        now,
                        now,
                    ),
                )
                self._connection.execute("COMMIT")
                return None
            except Exception:
                if self._connection.in_transaction:
                    self._connection.execute("ROLLBACK")
                raise

    def finish_operation(
        self,
        operation_id: str,
        *,
        status: OperationStatus,
        result_metadata: dict[str, Any] | None = None,
    ) -> None:
        if status is OperationStatus.IN_FLIGHT:
            raise ValueError("finish_operation requires a terminal local status")
        metadata = sanitize_value(result_metadata or {})
        encoded = json.dumps(metadata, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        with self._lock:
            cursor = self._connection.execute(
                """
                UPDATE operations
                   SET status = ?, result_metadata = ?, updated_at = ?
                 WHERE operation_id = ?
                """,
                (status.value, encoded, utc_now(), operation_id),
            )
            if cursor.rowcount != 1:
                raise InvalidStateError(
                    "RECOVERY_REQUIRED",
                    "The operation journal lost the in-flight operation record.",
                    next_action="Stop live writes and inspect the local journal.",
                )

    def save_case(self, case: CaseRecord) -> None:
        metadata = case.journal_metadata()
        with self._lock:
            self._connection.execute(
                """
                INSERT INTO cases (
                    case_id, experiment_id, category, policy_subcategory, state,
                    transcript_hash, prepared_hash, evaluation_id, conversation_id,
                    recovery_note, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(case_id) DO UPDATE SET
                    experiment_id = excluded.experiment_id,
                    category = excluded.category,
                    policy_subcategory = excluded.policy_subcategory,
                    state = excluded.state,
                    transcript_hash = excluded.transcript_hash,
                    prepared_hash = excluded.prepared_hash,
                    evaluation_id = excluded.evaluation_id,
                    conversation_id = excluded.conversation_id,
                    recovery_note = excluded.recovery_note,
                    updated_at = excluded.updated_at
                """,
                (
                    metadata["case_id"],
                    metadata["experiment_id"],
                    metadata["category"],
                    metadata["policy_subcategory"],
                    metadata["state"],
                    metadata["transcript_hash"],
                    metadata["prepared_payload_hash"],
                    metadata["evaluation_id"],
                    metadata["conversation_id"],
                    metadata["recovery_note"],
                    metadata["created_at"],
                    metadata["updated_at"],
                ),
            )

    def unfinished_cases(self) -> list[dict[str, str]]:
        with self._lock:
            rows = self._connection.execute(
                """
                SELECT case_id, experiment_id, category, policy_subcategory, state,
                       transcript_hash, prepared_hash, evaluation_id, conversation_id,
                       recovery_note, created_at, updated_at
                  FROM cases
                 WHERE state NOT IN (?, ?)
                 ORDER BY updated_at DESC
                """,
                ("CLOSED", "SUBMITTED"),
            ).fetchall()
        keys = (
            "case_id",
            "experiment_id",
            "category",
            "policy_subcategory",
            "state",
            "transcript_hash",
            "prepared_hash",
            "evaluation_id",
            "conversation_id",
            "recovery_note",
            "created_at",
            "updated_at",
        )
        return [dict(zip(keys, row, strict=True)) for row in rows]

    def submission_count(self) -> int:
        with self._lock:
            row = self._connection.execute(
                """
                SELECT COUNT(*)
                  FROM operations
                 WHERE tool_name = 'glacier_commit_submission'
                   AND status IN (?, ?, ?)
                """,
                (
                    OperationStatus.IN_FLIGHT.value,
                    OperationStatus.UNKNOWN.value,
                    OperationStatus.ACKNOWLEDGED.value,
                ),
            ).fetchone()
        return int(row[0]) if row else 0

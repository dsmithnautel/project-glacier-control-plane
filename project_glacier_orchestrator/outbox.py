"""Durable, content-addressed storage for sanitized pending task actions."""

from __future__ import annotations

import os
import re
import stat
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .canonical import (
    MAX_PACKET_BYTES,
    canonical_json_bytes,
    canonical_json_digest,
    sha256_hex,
    strict_json_loads,
)
from .checkpoint import secure_read_text
from .errors import OrchestratorError

OUTBOX_SCHEMA_VERSION = 1
OUTBOX_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,199}$")
MESSAGE_TYPE_PATTERN = re.compile(r"^[A-Z][A-Z0-9_]{0,63}$")


@dataclass(frozen=True)
class OutboxRecord:
    message_id: str
    message_type: str
    payload_digest: str
    payload: str | dict[str, Any]

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema_version": OUTBOX_SCHEMA_VERSION,
            "message_id": self.message_id,
            "message_type": self.message_type,
            "payload_digest": self.payload_digest,
            "payload": self.payload,
        }


def _validate_name(value: str, pattern: re.Pattern[str], *, code: str) -> str:
    if not isinstance(value, str) or pattern.fullmatch(value) is None:
        raise OrchestratorError(code, "An outbox identifier has an invalid format.")
    return value


def payload_digest(payload: str | dict[str, Any]) -> str:
    if isinstance(payload, str):
        try:
            return sha256_hex(payload.encode("utf-8"))
        except UnicodeEncodeError as exc:
            raise OrchestratorError("OUTBOX_PAYLOAD_INVALID", "The outbox payload is not valid UTF-8.") from exc
    if isinstance(payload, dict):
        return canonical_json_digest(payload)
    raise OrchestratorError("OUTBOX_PAYLOAD_INVALID", "The outbox payload must be a string or JSON object.")


class OutboxStore:
    """Write-once outbox records under one fixed workspace-owned directory."""

    def __init__(self, workspace_root: Path) -> None:
        self.state_directory = workspace_root / ".project-glacier-orchestrator"
        self.directory = self.state_directory / "outbox"

    def build_record(
        self,
        *,
        message_id: str,
        message_type: str,
        payload: str | dict[str, Any],
    ) -> OutboxRecord:
        return OutboxRecord(
            message_id=_validate_name(message_id, OUTBOX_ID_PATTERN, code="OUTBOX_MESSAGE_ID_INVALID"),
            message_type=_validate_name(message_type, MESSAGE_TYPE_PATTERN, code="OUTBOX_MESSAGE_TYPE_INVALID"),
            payload_digest=payload_digest(payload),
            payload=payload,
        )

    def put(self, record: OutboxRecord) -> None:
        self._ensure_directories()
        path = self.directory / f"{record.message_id}.json"
        encoded = canonical_json_bytes(record.as_dict())
        if len(encoded) > MAX_PACKET_BYTES:
            raise OrchestratorError("OUTBOX_RECORD_TOO_LARGE", "The outbox record exceeds the safe size limit.")
        flags = (
            os.O_WRONLY
            | os.O_CREAT
            | os.O_EXCL
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NOFOLLOW", 0)
        )
        try:
            fd = os.open(path, flags, 0o600)
        except FileExistsError:
            existing = self.load(record.message_id)
            if canonical_json_bytes(existing.as_dict()) != encoded:
                raise OrchestratorError(
                    "OUTBOX_ID_COLLISION",
                    "An outbox message ID already names different content.",
                ) from None
            return
        except OSError as exc:
            raise OrchestratorError("OUTBOX_OPEN_FAILED", "The outbox record cannot be opened safely.") from exc
        complete = False
        try:
            offset = 0
            while offset < len(encoded):
                written = os.write(fd, encoded[offset:])
                if written <= 0:
                    raise OrchestratorError("OUTBOX_WRITE_FAILED", "The outbox record could not be written completely.")
                offset += written
            os.fsync(fd)
            complete = True
        finally:
            os.close(fd)
            if not complete:
                try:
                    os.unlink(path)
                except FileNotFoundError:
                    pass
        try:
            directory_fd = os.open(self.directory, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        except OSError as exc:
            if complete:
                raise OrchestratorError(
                    "OUTBOX_COMMIT_AMBIGUOUS",
                    "The outbox file exists but durable directory synchronization could not be confirmed.",
                ) from exc
            raise

    def load(self, message_id: str) -> OutboxRecord:
        safe_id = _validate_name(message_id, OUTBOX_ID_PATTERN, code="OUTBOX_MESSAGE_ID_INVALID")
        packet = strict_json_loads(secure_read_text(self.directory / f"{safe_id}.json"))
        if set(packet) != {"schema_version", "message_id", "message_type", "payload_digest", "payload"}:
            raise OrchestratorError("OUTBOX_SCHEMA_INVALID", "The outbox record does not match its closed schema.")
        if packet["schema_version"] != OUTBOX_SCHEMA_VERSION or packet["message_id"] != safe_id:
            raise OrchestratorError("OUTBOX_SCHEMA_INVALID", "The outbox record identity or version is invalid.")
        message_type = _validate_name(
            packet["message_type"], MESSAGE_TYPE_PATTERN, code="OUTBOX_MESSAGE_TYPE_INVALID"
        )
        payload = packet["payload"]
        if not isinstance(payload, (str, dict)):
            raise OrchestratorError("OUTBOX_PAYLOAD_INVALID", "The outbox payload has an invalid type.")
        digest = payload_digest(payload)
        if packet["payload_digest"] != digest:
            raise OrchestratorError("OUTBOX_DIGEST_MISMATCH", "The outbox payload digest is invalid.")
        return OutboxRecord(safe_id, message_type, digest, payload)

    def _ensure_directories(self) -> None:
        for directory in (self.state_directory, self.directory):
            try:
                directory.mkdir(mode=0o700, exist_ok=True)
            except OSError as exc:
                raise OrchestratorError("OUTBOX_DIRECTORY_FAILED", "The outbox directory cannot be created safely.") from exc
            metadata = directory.stat(follow_symlinks=False)
            if not stat.S_ISDIR(metadata.st_mode) or stat.S_ISLNK(metadata.st_mode):
                raise OrchestratorError("OUTBOX_DIRECTORY_UNSAFE", "The outbox path is not a real directory.")
            if metadata.st_uid != os.getuid():
                raise OrchestratorError("OUTBOX_DIRECTORY_UNSAFE", "The outbox directory is not owned by this user.")
            os.chmod(directory, 0o700, follow_symlinks=False)

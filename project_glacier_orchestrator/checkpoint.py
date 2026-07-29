"""Crash-resistant parsing and compare-and-swap updates for the resume checkpoint."""

from __future__ import annotations

import fcntl
import json
import os
import re
import stat
import tempfile
from collections import OrderedDict
from collections.abc import Callable, Iterable, Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from .canonical import sha256_hex
from .errors import OrchestratorError

CHECKPOINT_HEADER = "PROJECT_GLACIER_RESUME"
HASH_KEY = "canonical_state_hash"
VERSION_KEY = "canonical_state_version"
TIMESTAMP_KEY = "checkpoint_generated_at"
DIGEST_PATTERN = re.compile(r"^[0-9a-f]{64}$")
MAX_ARTIFACT_BYTES = 8 * 1024 * 1024
MAX_CHECKPOINT_VALUE_BYTES = 256 * 1024


def _validate_checkpoint_value(value: object) -> None:
    if not isinstance(value, str):
        raise OrchestratorError("CHECKPOINT_VALUE_INVALID", "Checkpoint values must be strings.")
    try:
        encoded = value.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise OrchestratorError("CHECKPOINT_VALUE_INVALID", "A checkpoint value is not valid UTF-8.") from exc
    if len(encoded) > MAX_CHECKPOINT_VALUE_BYTES:
        raise OrchestratorError("CHECKPOINT_VALUE_TOO_LARGE", "A checkpoint value exceeds the safe size limit.")
    if value and value.splitlines() != [value]:
        raise OrchestratorError("CHECKPOINT_VALUE_INVALID", "Checkpoint values must remain single-line strings.")
    if any(ord(character) < 0x20 or 0x7F <= ord(character) <= 0x9F for character in value):
        raise OrchestratorError("CHECKPOINT_VALUE_INVALID", "Checkpoint values may not contain control characters.")


@dataclass(frozen=True)
class WorkspacePaths:
    """Fixed workspace paths; no path comes from a worker packet."""

    root: Path
    resume: Path
    rules: Path
    ledger: Path
    worker_prompt: Path
    config: Path
    lock: Path

    @classmethod
    def from_root(cls, root: Path) -> WorkspacePaths:
        resolved = root.resolve(strict=True)
        if not resolved.is_dir():
            raise OrchestratorError("WORKSPACE_NOT_DIRECTORY", "The workspace root is not a directory.")
        return cls(
            root=resolved,
            resume=resolved / "PROJECT_GLACIER_RESUME.txt",
            rules=resolved / "glacier_rules_dossier.md",
            ledger=resolved / "glacier_experiment_ledger.md",
            worker_prompt=resolved / "project_glacier_offline_root_worker_prompt.md",
            config=resolved / ".codex" / "config.toml",
            lock=resolved / ".project-glacier-orchestrator.lock",
        )


def secure_read_bytes(path: Path, *, max_bytes: int = MAX_ARTIFACT_BYTES) -> bytes:
    """Read one fixed regular file without following symlinks."""

    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        fd = os.open(path, flags)
    except OSError as exc:
        raise OrchestratorError("ARTIFACT_OPEN_FAILED", "A required workspace artifact cannot be opened safely.") from exc
    try:
        metadata_before = os.fstat(fd)
        if not stat.S_ISREG(metadata_before.st_mode) or metadata_before.st_nlink != 1:
            raise OrchestratorError("UNSAFE_ARTIFACT_TYPE", "A required workspace artifact is not a single regular file.")
        if metadata_before.st_size > max_bytes:
            raise OrchestratorError("ARTIFACT_TOO_LARGE", "A required workspace artifact exceeds the size limit.")
        chunks: list[bytes] = []
        remaining = max_bytes + 1
        while remaining > 0:
            chunk = os.read(fd, min(64 * 1024, remaining))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        data = b"".join(chunks)
        if len(data) > max_bytes:
            raise OrchestratorError("ARTIFACT_TOO_LARGE", "A required workspace artifact exceeds the size limit.")
        metadata_after = os.fstat(fd)
        identity_before = (
            metadata_before.st_dev,
            metadata_before.st_ino,
            metadata_before.st_size,
            metadata_before.st_mtime_ns,
            metadata_before.st_ctime_ns,
        )
        identity_after = (
            metadata_after.st_dev,
            metadata_after.st_ino,
            metadata_after.st_size,
            metadata_after.st_mtime_ns,
            metadata_after.st_ctime_ns,
        )
        if identity_before != identity_after or len(data) != metadata_after.st_size:
            raise OrchestratorError("ARTIFACT_CHANGED_DURING_READ", "A required workspace artifact changed while being read.")
        return data
    finally:
        os.close(fd)


def secure_read_text(path: Path, *, max_bytes: int = MAX_ARTIFACT_BYTES) -> str:
    try:
        return secure_read_bytes(path, max_bytes=max_bytes).decode("utf-8")
    except UnicodeDecodeError as exc:
        raise OrchestratorError("ARTIFACT_INVALID_UTF8", "A required workspace artifact is not valid UTF-8.") from exc


@dataclass
class ResumeDocument:
    """Ordered key/value checkpoint document that preserves unrelated live fields."""

    values: OrderedDict[str, str]

    @classmethod
    def parse(cls, text: str) -> ResumeDocument:
        normalized = text.replace("\r\n", "\n").replace("\r", "\n")
        lines = normalized.splitlines()
        if not lines or lines[0] != CHECKPOINT_HEADER:
            raise OrchestratorError("CHECKPOINT_HEADER_INVALID", "The resume checkpoint header is invalid.")
        values: OrderedDict[str, str] = OrderedDict()
        for line in lines[1:]:
            if not line:
                continue
            if line[:1].isspace() or ":" not in line:
                raise OrchestratorError("CHECKPOINT_LINE_INVALID", "The resume checkpoint contains an invalid line.")
            key, value = line.split(":", 1)
            if not key or not re.fullmatch(r"[a-z][a-z0-9_]*", key):
                raise OrchestratorError("CHECKPOINT_KEY_INVALID", "The resume checkpoint contains an invalid key.")
            if key in values:
                raise OrchestratorError("CHECKPOINT_DUPLICATE_KEY", "The resume checkpoint contains a duplicate key.")
            parsed_value = value.lstrip(" ")
            _validate_checkpoint_value(parsed_value)
            values[key] = parsed_value
        return cls(values)

    def clone(self) -> ResumeDocument:
        return ResumeDocument(OrderedDict(self.values))

    def render(self) -> str:
        for value in self.values.values():
            _validate_checkpoint_value(value)
        body = "".join(f"{key}: {value}\n" for key, value in self.values.items())
        return f"{CHECKPOINT_HEADER}\n{body}"

    def computed_hash(self) -> str:
        if HASH_KEY not in self.values:
            raise OrchestratorError("CHECKPOINT_HASH_MISSING", "The resume checkpoint has no canonical hash.")
        copy = self.clone()
        copy.values[HASH_KEY] = "OMITTED"
        return sha256_hex(copy.render().encode("utf-8"))

    def validate(self, *, required_keys: Iterable[str] = ()) -> None:
        missing = [key for key in required_keys if key not in self.values]
        if missing:
            raise OrchestratorError("CHECKPOINT_FIELD_MISSING", "The resume checkpoint is missing required coordination fields.")
        raw_version = self.values.get(VERSION_KEY, "")
        if not raw_version.isascii() or not raw_version.isdigit():
            raise OrchestratorError("CHECKPOINT_VERSION_INVALID", "The canonical state version is invalid.")
        stored_hash = self.values.get(HASH_KEY, "")
        if not DIGEST_PATTERN.fullmatch(stored_hash):
            raise OrchestratorError("CHECKPOINT_HASH_INVALID", "The canonical state hash is invalid.")
        if stored_hash != self.computed_hash():
            raise OrchestratorError("CHECKPOINT_HASH_MISMATCH", "The resume checkpoint hash does not match its contents.")
        timestamp = self.values.get(TIMESTAMP_KEY, "")
        try:
            datetime.strptime(timestamp, "%Y-%m-%dT%H:%M:%SZ")
        except ValueError as exc:
            raise OrchestratorError("CHECKPOINT_TIMESTAMP_INVALID", "The checkpoint timestamp is not UTC ISO-8601.") from exc

    @property
    def version(self) -> int:
        return int(self.values[VERSION_KEY])

    @property
    def state_hash(self) -> str:
        return self.values[HASH_KEY]

    def json_array(self, key: str, *, maximum: int) -> list[str]:
        try:
            value = json.loads(self.values[key])
        except (KeyError, json.JSONDecodeError) as exc:
            raise OrchestratorError("CHECKPOINT_ARRAY_INVALID", "A checkpoint replay field is not valid JSON.") from exc
        if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
            raise OrchestratorError("CHECKPOINT_ARRAY_INVALID", "A checkpoint replay field is not a string array.")
        if len(value) > maximum or len(value) != len(set(value)):
            raise OrchestratorError("CHECKPOINT_ARRAY_INVALID", "A checkpoint replay field violates its bounds or uniqueness rule.")
        return value


@dataclass(frozen=True)
class ArtifactDigests:
    rules: str
    ledger: str
    worker_prompt: str


class ResumeStore:
    """Single-writer checkpoint store using advisory locking plus hash/version CAS."""

    def __init__(
        self,
        paths: WorkspacePaths,
        *,
        required_keys: Iterable[str],
        state_validator: Callable[[ResumeDocument], None] | None = None,
    ) -> None:
        self.paths = paths
        self.required_keys = tuple(required_keys)
        self.state_validator = state_validator

    def read(self) -> ResumeDocument:
        document = ResumeDocument.parse(secure_read_text(self.paths.resume))
        document.validate(required_keys=self.required_keys)
        if self.state_validator is not None:
            self.state_validator(document)
        return document

    def artifact_digests(self) -> ArtifactDigests:
        return ArtifactDigests(
            rules=sha256_hex(secure_read_bytes(self.paths.rules)),
            ledger=sha256_hex(secure_read_bytes(self.paths.ledger)),
            worker_prompt=sha256_hex(secure_read_bytes(self.paths.worker_prompt)),
        )

    @contextmanager
    def _locked(self) -> Iterator[None]:
        flags = os.O_RDWR | os.O_CREAT | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
        try:
            fd = os.open(self.paths.lock, flags, 0o600)
        except OSError as exc:
            raise OrchestratorError("LOCK_OPEN_FAILED", "The orchestrator lock cannot be opened safely.") from exc
        try:
            metadata = os.fstat(fd)
            if not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1:
                raise OrchestratorError("LOCK_FILE_UNSAFE", "The orchestrator lock is not a single regular file.")
            fcntl.flock(fd, fcntl.LOCK_EX)
            path_metadata = os.stat(self.paths.lock, follow_symlinks=False)
            if (metadata.st_dev, metadata.st_ino) != (path_metadata.st_dev, path_metadata.st_ino):
                raise OrchestratorError("LOCK_FILE_REPLACED", "The orchestrator lock path changed while acquiring the lock.")
            yield
        finally:
            try:
                fcntl.flock(fd, fcntl.LOCK_UN)
            finally:
                os.close(fd)

    def update(
        self,
        *,
        expected_version: int,
        expected_hash: str,
        updates: Mapping[str, str],
        allowed_fields: frozenset[str],
        commit: bool,
        allow_rules_ledger_reconciliation: bool = False,
        allow_worker_prompt_reconciliation: bool = False,
    ) -> ResumeDocument:
        illegal = set(updates) - allowed_fields
        if illegal:
            raise OrchestratorError("PROTECTED_CHECKPOINT_FIELD", "The requested transition touches a protected checkpoint field.")
        if not commit:
            current = self.read()
            return self._prepare_next(
                current=current,
                expected_version=expected_version,
                expected_hash=expected_hash,
                updates=updates,
                allow_rules_ledger_reconciliation=allow_rules_ledger_reconciliation,
                allow_worker_prompt_reconciliation=allow_worker_prompt_reconciliation,
            )
        with self._locked():
            current = self.read()
            next_document = self._prepare_next(
                current=current,
                expected_version=expected_version,
                expected_hash=expected_hash,
                updates=updates,
                allow_rules_ledger_reconciliation=allow_rules_ledger_reconciliation,
                allow_worker_prompt_reconciliation=allow_worker_prompt_reconciliation,
            )
            rendered = next_document.render()
            self._verify_planned_artifact_digests(next_document)
            self._atomic_write(rendered)
            reread = self.read()
            if reread.render() != rendered:
                raise OrchestratorError("CHECKPOINT_VERIFY_FAILED", "The durable checkpoint did not match the planned transition.")
            self._verify_planned_artifact_digests(reread)
            return reread

    def _verify_planned_artifact_digests(self, document: ResumeDocument) -> None:
        digests = self.artifact_digests()
        if (
            document.values["rules_digest"] != digests.rules
            or document.values["ledger_digest"] != digests.ledger
            or document.values["offline_worker_prompt_digest"] != digests.worker_prompt
        ):
            raise OrchestratorError(
                "ARTIFACT_CHANGED_DURING_TRANSITION",
                "A canonical protocol artifact changed during the checkpoint transition.",
            )

    def _prepare_next(
        self,
        *,
        current: ResumeDocument,
        expected_version: int,
        expected_hash: str,
        updates: Mapping[str, str],
        allow_rules_ledger_reconciliation: bool,
        allow_worker_prompt_reconciliation: bool,
    ) -> ResumeDocument:
        if current.version != expected_version or current.state_hash != expected_hash:
            raise OrchestratorError("CHECKPOINT_CAS_CONFLICT", "The checkpoint changed since the caller read it.")
        digests = self.artifact_digests()
        if (
            current.values["offline_worker_prompt_digest"] != digests.worker_prompt
            and not allow_worker_prompt_reconciliation
        ):
            raise OrchestratorError("WORKER_PROMPT_DRIFT", "The worker prompt differs from the checkpointed protocol artifact.")
        rules_or_ledger_changed = (
            current.values["rules_digest"] != digests.rules
            or current.values["ledger_digest"] != digests.ledger
        )
        if rules_or_ledger_changed and not allow_rules_ledger_reconciliation:
            raise OrchestratorError(
                "CANONICAL_ARTIFACT_DRIFT",
                "Canonical rules or ledger bytes changed outside an explicit reconciliation transition.",
            )
        next_document = current.clone()
        for key, value in updates.items():
            _validate_checkpoint_value(value)
            next_document.values[key] = value
        next_document.values["rules_digest"] = digests.rules
        next_document.values["ledger_digest"] = digests.ledger
        next_document.values[VERSION_KEY] = str(current.version + 1)
        next_document.values[TIMESTAMP_KEY] = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
        next_document.values[HASH_KEY] = "0" * 64
        next_document.values[HASH_KEY] = next_document.computed_hash()
        rendered = next_document.render()
        if len(rendered.encode("utf-8")) > MAX_ARTIFACT_BYTES:
            raise OrchestratorError("CHECKPOINT_TOO_LARGE", "The planned checkpoint exceeds the safe size limit.")
        reparsed = ResumeDocument.parse(rendered)
        reparsed.validate(required_keys=self.required_keys)
        if self.state_validator is not None:
            self.state_validator(reparsed)
        return reparsed

    def _atomic_write(self, text: str) -> None:
        directory = self.paths.resume.parent
        fd, temporary_name = tempfile.mkstemp(prefix=".PROJECT_GLACIER_RESUME.", dir=directory)
        replaced = False
        try:
            os.fchmod(fd, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
                handle.write(text)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary_name, self.paths.resume)
            replaced = True
            directory_flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
            directory_fd = os.open(directory, directory_flags)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        except Exception as exc:
            try:
                os.close(fd)
            except OSError:
                pass
            if not replaced:
                try:
                    os.unlink(temporary_name)
                except FileNotFoundError:
                    pass
                raise
            raise OrchestratorError(
                "CHECKPOINT_COMMIT_AMBIGUOUS",
                "The checkpoint replacement occurred but durable directory synchronization could not be confirmed.",
            ) from exc

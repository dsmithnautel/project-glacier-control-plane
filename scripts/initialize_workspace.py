#!/usr/bin/env python3
"""Initialize neutral, fail-closed Project Glacier runtime artifacts.

This command is deliberately local-only. It reads versioned repository templates and the offline
worker prompt, then creates a new set of private runtime files without replacing anything already
present in the target workspace.
"""

from __future__ import annotations

import argparse
import hashlib
import os
import secrets
import stat
import sys
from collections import OrderedDict
from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
TEMPLATE_ROOT = REPOSITORY_ROOT / "templates" / "runtime"
WORKER_PROMPT_NAME = "project_glacier_offline_root_worker_prompt.md"
WORKER_PROMPT_VERSION = "2026-07-12-v1"
RESUME_NAME = "PROJECT_GLACIER_RESUME.txt"
CHECKPOINT_HEADER = "PROJECT_GLACIER_RESUME"
HASH_KEY = "canonical_state_hash"
MAX_SOURCE_BYTES = 8 * 1024 * 1024
PRIVATE_FILE_MODE = 0o600
RUNTIME_TEMPLATE_NAMES = (
    "CURRENT_CHECKPOINT.md",
    "glacier_rules_dossier.md",
    "glacier_experiment_ledger.md",
    "glacier_coverage_matrix.md",
    "glacier_hypothesis_registry.md",
    "glacier_findings_queue.md",
    "glacier_artifact_index.md",
)


class InitializationError(RuntimeError):
    """A safe workspace initialization could not be completed."""


def _sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _required_open_flag(name: str) -> int:
    if not hasattr(os, name):
        raise InitializationError(f"The platform lacks the required filesystem flag {name}.")
    return int(getattr(os, name))


def _read_source_descriptor(file_descriptor: int, label: str) -> bytes:
    """Validate and read one already-opened source descriptor."""

    metadata_before = os.fstat(file_descriptor)
    if not stat.S_ISREG(metadata_before.st_mode) or metadata_before.st_nlink != 1:
        raise InitializationError(
            f"Required source artifact is not a single regular file: {label}"
        )
    if metadata_before.st_size > MAX_SOURCE_BYTES:
        raise InitializationError(
            f"Required source artifact exceeds the size limit: {label}"
        )
    chunks: list[bytes] = []
    remaining = MAX_SOURCE_BYTES + 1
    while remaining > 0:
        chunk = os.read(file_descriptor, min(64 * 1024, remaining))
        if not chunk:
            break
        chunks.append(chunk)
        remaining -= len(chunk)
    content = b"".join(chunks)
    metadata_after = os.fstat(file_descriptor)
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
    if (
        len(content) > MAX_SOURCE_BYTES
        or len(content) != metadata_after.st_size
        or identity_before != identity_after
    ):
        raise InitializationError(f"Required source artifact changed during read: {label}")
    return content


def _secure_read(path: Path) -> bytes:
    flags = os.O_RDONLY | _required_open_flag("O_NOFOLLOW")
    flags |= getattr(os, "O_CLOEXEC", 0)
    try:
        file_descriptor = os.open(path, flags)
    except OSError as exc:
        raise InitializationError(
            f"Required source artifact cannot be opened safely: {path.name}"
        ) from exc
    try:
        return _read_source_descriptor(file_descriptor, path.name)
    finally:
        os.close(file_descriptor)


def _secure_read_at(directory_descriptor: int, name: str) -> bytes:
    """Read a source relative to the pinned workspace descriptor."""

    flags = os.O_RDONLY | _required_open_flag("O_NOFOLLOW")
    flags |= getattr(os, "O_CLOEXEC", 0)
    try:
        file_descriptor = os.open(name, flags, dir_fd=directory_descriptor)
    except OSError as exc:
        raise InitializationError(
            f"Required source artifact cannot be opened safely: {name}"
        ) from exc
    try:
        return _read_source_descriptor(file_descriptor, name)
    finally:
        os.close(file_descriptor)


def _absolute_without_symlinks(path: Path) -> Path:
    expanded = path.expanduser()
    absolute = expanded if expanded.is_absolute() else Path.cwd() / expanded
    current = Path(absolute.anchor)
    for component in absolute.parts[1:]:
        current /= component
        try:
            metadata = current.lstat()
        except OSError as exc:
            raise InitializationError(
                "The target workspace must already exist and be accessible."
            ) from exc
        if stat.S_ISLNK(metadata.st_mode):
            raise InitializationError("The target workspace path may not contain symbolic links.")
    if not absolute.is_dir():
        raise InitializationError("The target workspace is not a directory.")
    return absolute


def _render_resume(values: Mapping[str, str]) -> str:
    body = "".join(f"{key}: {value}\n" for key, value in values.items())
    return f"{CHECKPOINT_HEADER}\n{body}"


def _resume_bytes(
    *,
    generated_at: datetime,
    rules_digest: str,
    ledger_digest: str,
    worker_prompt_digest: str,
) -> bytes:
    if generated_at.tzinfo is None or generated_at.utcoffset() is None:
        raise InitializationError("The checkpoint timestamp must be timezone-aware.")
    timestamp = generated_at.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    values: OrderedDict[str, str] = OrderedDict(
        [
            ("rules_version", "unknown"),
            ("run_mode", "NOT_STARTED"),
            ("orchestration_mode", "SINGLE_ROOT"),
            ("multi_root_protocol_version", "2"),
            ("multi_root_variant", "OFFLINE_WORKER_DISABLED"),
            ("canonical_state_version", "1"),
            (HASH_KEY, "0" * 64),
            ("checkpoint_generated_at", timestamp),
            ("checkpoint_reconciliation_required", "true"),
            ("global_mutation_lock", "checkpoint-reconciliation-required"),
            ("multi_root_leadership_epoch", "0"),
            ("multi_root_replay_window_size", "256"),
            ("multi_root_processed_message_ids", "[]"),
            ("multi_root_processed_result_ids", "[]"),
            ("rules_digest", rules_digest),
            ("ledger_digest", ledger_digest),
            ("offline_worker_thread_id", "none"),
            ("offline_worker_host_id", "none"),
            ("offline_worker_prompt_version", WORKER_PROMPT_VERSION),
            ("offline_worker_prompt_digest", worker_prompt_digest),
            ("offline_worker_bootstrap_message_id", "none"),
            ("offline_worker_attested_prompt_version", "none"),
            ("offline_worker_attested_prompt_digest", "none"),
            ("offline_worker_pending_message_id", "none"),
            ("offline_worker_expected_response_type", "NONE"),
            ("offline_worker_status", "UNREGISTERED"),
            ("offline_worker_last_ack_version", "none"),
            ("next_worker_assignment_sequence", "1"),
            ("active_worker_assignment_id", "none"),
            ("active_worker_assignment_message_id", "none"),
            ("active_worker_assignment_base_version", "none"),
            ("active_worker_assignment_base_hash", "none"),
            ("active_worker_assignment_rules_digest", "none"),
            ("active_worker_assignment_ledger_digest", "none"),
            ("active_worker_assignment_live_state_digest", "none"),
            ("pending_worker_result_id", "none"),
            ("pending_worker_result_digest", "none"),
            ("pending_worker_result_assignment_id", "none"),
            ("last_accepted_worker_result_id", "none"),
            ("last_worker_result_disposition", "none"),
            ("coordination_outbox_message_id", "none"),
            ("coordination_outbox_message_type", "none"),
            ("coordination_outbox_payload_digest", "none"),
            ("coordination_outbox_delivery_status", "NONE"),
            ("worker_coordination_blocker", "checkpoint-reconciliation-required"),
            ("multi_root_last_capacity_decision", "not-evaluated"),
            ("authorized_target", "unknown"),
            ("challenge_transport", "registered-project-glacier-mcp-only"),
            ("mcp_server", "project_glacier"),
            ("submission_enabled", "unknown"),
            ("submission_count", "unknown"),
            ("submission_limit", "unknown"),
            ("remaining_submission_budget", "unknown"),
            ("ledger_location", "glacier_experiment_ledger.md"),
            ("last_completed_experiment", "none"),
            ("active_case_id", "none"),
            ("active_case_state", "none"),
            ("transcript_hash", "none"),
            ("last_operation_id", "none"),
            ("last_delivery_status", "unknown"),
            ("active_blocker_type", "CHECKPOINT_RECONCILIATION_REQUIRED"),
            (
                "blocker_evidence",
                "server-authoritative broker and budget state have not been reconciled",
            ),
            ("safe_actions_attempted", "local neutral artifact initialization only"),
            ("mutations_performed_during_diagnosis", "none"),
            (
                "manual_action_required",
                "reconcile canonical files and broker state under the base master protocol",
            ),
            ("prepared_payload_hash", "none"),
            ("submission_receipt_ids", "[]"),
            ("validated_findings", "[]"),
            ("leading_preliminary_candidates", "[]"),
            ("blocked_families_and_reopen_conditions", "[]"),
            ("coverage_gaps", "all policy rows"),
            ("capacity_forecast", "unknown"),
            ("bottleneck_alerts", "broker state and budget unknown"),
            ("authoritative_budget_source", "unknown"),
            ("active_or_last_agent_assignments", "[]"),
            (
                "next_five_actions",
                '["load protocol","inspect artifacts","open registered broker session",'
                '"reconcile authoritative state","checkpoint before mutation"]',
            ),
            (
                "exact_next_action",
                "reconcile checkpoint and server-authoritative broker state before mutation",
            ),
            ("submission_queue", "none"),
            ("unresolved_scope_questions", "authorized target and broker state are unknown"),
        ]
    )
    hash_view = OrderedDict(values)
    hash_view[HASH_KEY] = "OMITTED"
    values[HASH_KEY] = _sha256_hex(_render_resume(hash_view).encode("utf-8"))
    return _render_resume(values).encode("utf-8")


def _open_workspace(path: Path) -> tuple[Path, int]:
    absolute = _absolute_without_symlinks(path)
    flags = os.O_RDONLY | _required_open_flag("O_DIRECTORY")
    flags |= _required_open_flag("O_NOFOLLOW")
    flags |= getattr(os, "O_CLOEXEC", 0)
    try:
        directory_descriptor = os.open(absolute, flags)
    except OSError as exc:
        raise InitializationError("The target workspace cannot be opened safely.") from exc
    metadata = os.fstat(directory_descriptor)
    if not stat.S_ISDIR(metadata.st_mode):
        os.close(directory_descriptor)
        raise InitializationError("The target workspace is not a directory.")
    if metadata.st_uid != os.geteuid():
        os.close(directory_descriptor)
        raise InitializationError("The target workspace must be owned by the current user.")
    if stat.S_IMODE(metadata.st_mode) & 0o022:
        os.close(directory_descriptor)
        raise InitializationError(
            "The target workspace must not be writable by group or other users."
        )
    return absolute, directory_descriptor


def _verify_workspace_identity(path: Path, directory_descriptor: int) -> None:
    """Fail if the workspace pathname no longer names the opened directory."""

    try:
        path_metadata = path.stat(follow_symlinks=False)
        descriptor_metadata = os.fstat(directory_descriptor)
    except OSError as exc:
        raise InitializationError("The target workspace identity changed.") from exc
    if (
        not stat.S_ISDIR(path_metadata.st_mode)
        or path_metadata.st_dev != descriptor_metadata.st_dev
        or path_metadata.st_ino != descriptor_metadata.st_ino
    ):
        raise InitializationError("The target workspace identity changed.")


def _destination_exists(directory_descriptor: int, name: str) -> bool:
    try:
        os.stat(name, dir_fd=directory_descriptor, follow_symlinks=False)
    except FileNotFoundError:
        return False
    except OSError as exc:
        raise InitializationError(f"Cannot safely inspect destination: {name}") from exc
    return True


def _write_all(file_descriptor: int, content: bytes) -> None:
    view = memoryview(content)
    while view:
        written = os.write(file_descriptor, view)
        if written <= 0:
            raise InitializationError("A staged runtime artifact could not be written completely.")
        view = view[written:]


def _atomic_create_files(
    directory_descriptor: int,
    files: Mapping[str, bytes],
    *,
    verify_workspace: Callable[[], None] | None = None,
) -> None:
    for name in files:
        if _destination_exists(directory_descriptor, name):
            raise InitializationError(f"Refusing to replace existing runtime artifact: {name}")

    staged: dict[str, tuple[str, tuple[int, int], int]] = {}
    created: list[tuple[str, tuple[int, int]]] = []
    temporary_names: set[str] = set()
    try:
        for destination, content in files.items():
            temporary = f".project-glacier-init-{secrets.token_hex(16)}"
            flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_CLOEXEC", 0)
            flags |= _required_open_flag("O_NOFOLLOW")
            file_descriptor = os.open(
                temporary,
                flags,
                PRIVATE_FILE_MODE,
                dir_fd=directory_descriptor,
            )
            temporary_names.add(temporary)
            try:
                os.fchmod(file_descriptor, PRIVATE_FILE_MODE)
                _write_all(file_descriptor, content)
                os.fsync(file_descriptor)
                metadata = os.fstat(file_descriptor)
                if (
                    not stat.S_ISREG(metadata.st_mode)
                    or metadata.st_nlink != 1
                    or stat.S_IMODE(metadata.st_mode) != PRIVATE_FILE_MODE
                    or metadata.st_size != len(content)
                ):
                    raise InitializationError("A staged runtime artifact failed validation.")
                identity = (metadata.st_dev, metadata.st_ino)
            finally:
                os.close(file_descriptor)
            staged[destination] = (temporary, identity, len(content))

        if verify_workspace is not None:
            verify_workspace()
        for destination, (temporary, identity, expected_size) in staged.items():
            if verify_workspace is not None:
                verify_workspace()
            source_metadata = os.stat(
                temporary,
                dir_fd=directory_descriptor,
                follow_symlinks=False,
            )
            if (
                not stat.S_ISREG(source_metadata.st_mode)
                or (source_metadata.st_dev, source_metadata.st_ino) != identity
                or source_metadata.st_nlink != 1
                or stat.S_IMODE(source_metadata.st_mode) != PRIVATE_FILE_MODE
                or source_metadata.st_size != expected_size
            ):
                raise InitializationError("A staged runtime artifact changed before publication.")

            # Record the exact inode before publishing. If interruption occurs
            # immediately after link(2), rollback can still identify it.
            created.append((destination, identity))
            os.link(
                temporary,
                destination,
                src_dir_fd=directory_descriptor,
                dst_dir_fd=directory_descriptor,
                follow_symlinks=False,
            )
            destination_metadata = os.stat(
                destination,
                dir_fd=directory_descriptor,
                follow_symlinks=False,
            )
            published_identity = (
                destination_metadata.st_dev,
                destination_metadata.st_ino,
            )
            if (
                not stat.S_ISREG(destination_metadata.st_mode)
                or published_identity != identity
                or destination_metadata.st_nlink != 2
                or stat.S_IMODE(destination_metadata.st_mode) != PRIVATE_FILE_MODE
                or destination_metadata.st_size != expected_size
            ):
                raise InitializationError("A published runtime artifact failed validation.")
            os.unlink(temporary, dir_fd=directory_descriptor)
            temporary_names.discard(temporary)
        os.fsync(directory_descriptor)
        if verify_workspace is not None:
            verify_workspace()
    except BaseException as exc:
        for destination, identity in reversed(created):
            try:
                metadata = os.stat(
                    destination,
                    dir_fd=directory_descriptor,
                    follow_symlinks=False,
                )
                if (metadata.st_dev, metadata.st_ino) == identity:
                    os.unlink(destination, dir_fd=directory_descriptor)
            except FileNotFoundError:
                pass
            except OSError:
                pass
        try:
            os.fsync(directory_descriptor)
        except OSError:
            pass
        if isinstance(exc, InitializationError):
            raise
        if isinstance(exc, Exception):
            raise InitializationError(
                "Runtime artifacts could not be created as a complete set."
            ) from exc
        raise
    finally:
        for temporary in temporary_names:
            try:
                os.unlink(temporary, dir_fd=directory_descriptor)
            except FileNotFoundError:
                pass
            except OSError:
                pass


def initialize_workspace(
    workspace: Path,
    *,
    generated_at: datetime | None = None,
) -> tuple[Path, ...]:
    """Create neutral runtime artifacts in an existing workspace without overwriting files."""

    template_content = {
        name: _secure_read(TEMPLATE_ROOT / name) for name in RUNTIME_TEMPLATE_NAMES
    }
    workspace_path, directory_descriptor = _open_workspace(workspace)
    try:
        _verify_workspace_identity(workspace_path, directory_descriptor)
        prompt_content = _secure_read_at(directory_descriptor, WORKER_PROMPT_NAME)
        resume_content = _resume_bytes(
            generated_at=generated_at or datetime.now(UTC),
            rules_digest=_sha256_hex(template_content["glacier_rules_dossier.md"]),
            ledger_digest=_sha256_hex(template_content["glacier_experiment_ledger.md"]),
            worker_prompt_digest=_sha256_hex(prompt_content),
        )
        output_content: OrderedDict[str, bytes] = OrderedDict(
            (name, template_content[name]) for name in RUNTIME_TEMPLATE_NAMES
        )
        output_content[RESUME_NAME] = resume_content
        _verify_workspace_identity(workspace_path, directory_descriptor)
        _atomic_create_files(
            directory_descriptor,
            output_content,
            verify_workspace=lambda: _verify_workspace_identity(
                workspace_path,
                directory_descriptor,
            ),
        )
    finally:
        os.close(directory_descriptor)
    return tuple(workspace_path / name for name in (*RUNTIME_TEMPLATE_NAMES, RESUME_NAME))


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Create private, fail-closed Project Glacier runtime artifacts. "
            "Existing destinations are never replaced."
        )
    )
    parser.add_argument(
        "--workspace",
        type=Path,
        default=REPOSITORY_ROOT,
        help="existing workspace containing the versioned offline worker prompt",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    try:
        created = initialize_workspace(arguments.workspace)
    except InitializationError as exc:
        print(f"initialization failed: {exc}", file=sys.stderr)
        return 1
    print(f"initialized {len(created)} private runtime artifacts in {created[0].parent}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

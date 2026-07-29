"""Declarative Codex task actions for R0 to execute through model-visible app tools."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class TaskAction:
    """A non-executing action envelope with explicit recovery instructions."""

    action_id: str
    tool: str
    arguments: dict[str, Any]
    checkpoint_version: int
    checkpoint_hash: str
    outbox_message_type: str
    outbox_payload_digest: str
    success_command: str
    known_failure_command: str
    uncertain_command: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "action_schema_version": 1,
            "action_id": self.action_id,
            "tool": self.tool,
            "arguments": self.arguments,
            "checkpoint_precondition": {
                "canonical_state_version": self.checkpoint_version,
                "canonical_state_hash": self.checkpoint_hash,
                "outbox_message_id": self.action_id,
                "outbox_message_type": self.outbox_message_type,
                "outbox_payload_digest": self.outbox_payload_digest,
                "outbox_delivery_status": "PLANNED",
            },
            "after_success": self.success_command,
            "after_known_failure": self.known_failure_command,
            "after_timeout_or_unknown": self.uncertain_command,
        }


def create_thread_action(
    *,
    action_id: str,
    bootstrap_prompt: str,
    payload_digest: str,
    checkpoint_version: int,
    checkpoint_hash: str,
) -> TaskAction:
    return TaskAction(
        action_id=action_id,
        tool="codex_app__create_thread",
        arguments={"prompt": bootstrap_prompt, "target": {"type": "projectless"}},
        checkpoint_version=checkpoint_version,
        checkpoint_hash=checkpoint_hash,
        outbox_message_type="CREATE_THREAD",
        outbox_payload_digest=payload_digest,
        success_command=(
            "Run record-worker-created with the returned threadId and hostId (use 'none' if no hostId is returned)."
        ),
        known_failure_command="Run record-delivery with --delivery-status REJECTED; do not claim success.",
        uncertain_command=(
            "Run record-delivery with --delivery-status UNKNOWN, then inspect with list_threads/read_thread; never blind-retry creation."
        ),
    )


def send_message_action(
    *,
    action_id: str,
    prompt: str,
    thread_id: str,
    host_id: str | None,
    checkpoint_version: int,
    checkpoint_hash: str,
    message_type: str,
    payload_digest: str,
) -> TaskAction:
    arguments: dict[str, Any] = {"threadId": thread_id, "prompt": prompt}
    if host_id is not None:
        arguments["hostId"] = host_id
    return TaskAction(
        action_id=action_id,
        tool="codex_app__send_message_to_thread",
        arguments=arguments,
        checkpoint_version=checkpoint_version,
        checkpoint_hash=checkpoint_hash,
        outbox_message_type=message_type,
        outbox_payload_digest=payload_digest,
        success_command="Run record-delivery with --delivery-status ACKNOWLEDGED.",
        known_failure_command="Run record-delivery with --delivery-status REJECTED; do not claim success.",
        uncertain_command=(
            "Run record-delivery with --delivery-status UNKNOWN; do not resend until the task transcript proves non-delivery."
        ),
    )


def read_thread_action(*, thread_id: str, host_id: str | None) -> dict[str, Any]:
    arguments: dict[str, Any] = {
        "threadId": thread_id,
        "turnLimit": 4,
        "includeOutputs": False,
    }
    if host_id is not None:
        arguments["hostId"] = host_id
    return {"tool": "codex_app__read_thread", "arguments": arguments}


def list_threads_action(*, query: str) -> dict[str, Any]:
    return {
        "tool": "codex_app__list_threads",
        "arguments": {"query": query, "limit": 20},
    }


def archive_thread_action(
    *,
    action_id: str,
    thread_id: str,
    payload_digest: str,
    checkpoint_version: int,
    checkpoint_hash: str,
) -> TaskAction:
    return TaskAction(
        action_id=action_id,
        tool="codex_app__set_thread_archived",
        arguments={"threadId": thread_id, "archived": True},
        checkpoint_version=checkpoint_version,
        checkpoint_hash=checkpoint_hash,
        outbox_message_type="ARCHIVE_THREAD",
        outbox_payload_digest=payload_digest,
        success_command="Run record-archived with --delivery-status ACKNOWLEDGED.",
        known_failure_command="Run record-archived with --delivery-status REJECTED; do not claim success.",
        uncertain_command=(
            "Run record-archived with --delivery-status UNKNOWN, inspect the task, and never infer archival from a timeout."
        ),
    )

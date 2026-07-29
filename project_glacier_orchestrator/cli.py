"""Secret-safe command-line interface for the offline-root coordinator."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from .canonical import MAX_PACKET_BYTES, strict_json_loads
from .checkpoint import secure_read_text
from .errors import OrchestratorError
from .protocol import ACK_DISPOSITIONS, STOP_REASONS
from .workflow import Coordinator, Transition


def _read_input(path: str) -> str:
    if path == "-":
        data = sys.stdin.buffer.read(MAX_PACKET_BYTES + 1)
        if len(data) > MAX_PACKET_BYTES:
            raise OrchestratorError("PACKET_TOO_LARGE", "The input exceeds the configured size limit.")
        try:
            return data.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise OrchestratorError("ARTIFACT_INVALID_UTF8", "The input is not valid UTF-8.") from exc
    return secure_read_text(Path(path), max_bytes=MAX_PACKET_BYTES)


def _emit(value: dict[str, Any]) -> None:
    sys.stdout.write(json.dumps(value, ensure_ascii=True, indent=2, sort_keys=True))
    sys.stdout.write("\n")


def _add_fence(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--expected-version", type=int, required=True)
    parser.add_argument("--expected-hash", required=True)
    parser.add_argument("--commit", action="store_true", help="Persist the planned checkpoint transition.")


def _add_packet(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--packet-file",
        default="-",
        help="UTF-8 packet file, or '-' for stdin. Raw packet text is never echoed on errors.",
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="project-glacier-orchestrator",
        description="Plan and checkpoint one isolated Project Glacier offline worker without calling external tools.",
    )
    parser.add_argument("--workspace", default=".", help=argparse.SUPPRESS)
    commands = parser.add_subparsers(dest="command", required=True)

    commands.add_parser("doctor", help="Validate local protocol/checkpoint compatibility without mutation.")
    commands.add_parser("pending-action", help="Reconstruct the exact safely planned Codex task action.")
    commands.add_parser("diagnose-unknown", help="Emit read-only actions for an unknown task-action outcome.")

    reconcile = commands.add_parser("reconcile-prompt", help="Adopt changed worker-prompt bytes before worker creation.")
    _add_fence(reconcile)

    activate = commands.add_parser("activate-pilot", help="Durably enter multi-root mode after explicit user authorization.")
    _add_fence(activate)
    activate.add_argument("--user-authorized-offline-worker", action="store_true", required=True)

    deactivate = commands.add_parser("deactivate-pilot", help="Restore single-root mode after worker cleanup.")
    _add_fence(deactivate)

    bootstrap = commands.add_parser("plan-bootstrap", help="Plan creation of the one projectless worker task.")
    _add_fence(bootstrap)

    created = commands.add_parser("record-worker-created", help="Bind successful task creation to its returned identity.")
    _add_fence(created)
    created.add_argument("--thread-id", required=True)
    created.add_argument("--host-id", default="none")
    created.add_argument("--ambiguity-resolved", action="store_true")

    delivery = commands.add_parser("record-delivery", help="Record a planned message/create task-action outcome.")
    _add_fence(delivery)
    delivery.add_argument("--message-id", required=True)
    delivery.add_argument("--delivery-status", choices=("ACKNOWLEDGED", "UNKNOWN", "REJECTED"), required=True)
    delivery.add_argument(
        "--delivery-evidence-verified",
        action="store_true",
        help="Attest exact task evidence when changing an UNKNOWN or REJECTED outcome.",
    )

    control_complete = commands.add_parser(
        "complete-control-turn",
        help="Mark an acknowledged ACK/rebase turn idle after read-only task verification.",
    )
    _add_fence(control_complete)
    control_complete.add_argument("--source-thread-id", required=True)
    control_complete.add_argument("--task-inactive", action="store_true", required=True)

    requeue = commands.add_parser(
        "requeue-known-nondelivery",
        help="Return the exact outbox action to PLANNED only after verified nondelivery.",
    )
    _add_fence(requeue)
    requeue.add_argument("--nondelivery-verified", action="store_true", required=True)
    requeue.add_argument("--task-inactive", action="store_true", required=True)

    attestation = commands.add_parser("accept-attestation", help="Validate and checkpoint the worker isolation attestation.")
    _add_fence(attestation)
    attestation.add_argument("--source-thread-id", required=True)
    attestation.add_argument("--task-inactive", action="store_true", required=True)
    _add_packet(attestation)

    assignment = commands.add_parser("plan-assignment", help="Validate and plan one sanitized offline assignment.")
    _add_fence(assignment)
    assignment.add_argument("--spec-file", default="-", help="Closed assignment-spec JSON file, or '-' for stdin.")

    result = commands.add_parser("accept-result", help="Validate, fence, and checkpoint one worker result.")
    _add_fence(result)
    result.add_argument("--source-thread-id", required=True)
    result.add_argument("--task-inactive", action="store_true", required=True)
    _add_packet(result)

    verify_result = commands.add_parser(
        "verify-pending-result",
        help="Read-only recovery verification for an already checkpointed result packet.",
    )
    verify_result.add_argument("--source-thread-id", required=True)
    verify_result.add_argument("--task-inactive", action="store_true", required=True)
    _add_packet(verify_result)

    rejected = commands.add_parser("accept-protocol-reject", help="Record a correlated worker protocol rejection.")
    _add_fence(rejected)
    rejected.add_argument("--source-thread-id", required=True)
    rejected.add_argument("--task-inactive", action="store_true", required=True)
    _add_packet(rejected)

    commit_result = commands.add_parser("commit-result", help="Checkpoint R0's semantic disposition after artifact review.")
    _add_fence(commit_result)
    commit_result.add_argument("--disposition", choices=tuple(sorted(ACK_DISPOSITIONS)), required=True)
    commit_result.add_argument(
        "--artifacts-reviewed",
        action="store_true",
        help="Explicitly attest that intentional rules/ledger byte changes were reviewed.",
    )

    ack = commands.add_parser("plan-ack", help="Build an ACK referencing the already durable disposition checkpoint.")
    _add_fence(ack)

    stop = commands.add_parser("plan-stop", help="Plan STOP_ALL; CLEANUP is the normal final reason.")
    _add_fence(stop)
    stop.add_argument("--reason-code", choices=tuple(sorted(STOP_REASONS)), default="CLEANUP")

    stop_ack = commands.add_parser("accept-stop-ack", help="Validate STOP_ACK and plan archive after inactive verification.")
    _add_fence(stop_ack)
    stop_ack.add_argument("--source-thread-id", required=True)
    stop_ack.add_argument("--task-inactive", action="store_true", required=True)
    _add_packet(stop_ack)

    archived = commands.add_parser("record-archived", help="Record the Codex archive action outcome.")
    _add_fence(archived)
    archived.add_argument("--delivery-status", choices=("ACKNOWLEDGED", "UNKNOWN", "REJECTED"), required=True)
    archived.add_argument(
        "--delivery-evidence-verified",
        action="store_true",
        help="Attest exact task evidence when changing an UNKNOWN or REJECTED outcome.",
    )

    return parser


def _transition(value: Transition) -> dict[str, Any]:
    return value.as_dict()


def dispatch(args: argparse.Namespace) -> dict[str, Any]:
    coordinator = Coordinator(Path(args.workspace))
    command = args.command
    if command == "doctor":
        return coordinator.doctor()
    if command == "pending-action":
        return coordinator.pending_action()
    if command == "diagnose-unknown":
        return coordinator.unknown_delivery_diagnostics()
    if command == "verify-pending-result":
        return coordinator.verify_pending_result(
            source_thread_id=args.source_thread_id,
            packet_text=_read_input(args.packet_file),
            task_inactive=args.task_inactive,
        )
    fence = {
        "expected_version": args.expected_version,
        "expected_hash": args.expected_hash,
        "commit": args.commit,
    }
    if command == "reconcile-prompt":
        return _transition(coordinator.reconcile_prompt(**fence))
    if command == "activate-pilot":
        return _transition(
            coordinator.activate_pilot(
                **fence,
                user_authorized_offline_worker=args.user_authorized_offline_worker,
            )
        )
    if command == "deactivate-pilot":
        return _transition(coordinator.deactivate_pilot(**fence))
    if command == "plan-bootstrap":
        return _transition(coordinator.plan_bootstrap(**fence))
    if command == "record-worker-created":
        return _transition(
            coordinator.record_worker_created(
                **fence,
                thread_id=args.thread_id,
                host_id=args.host_id,
                ambiguity_resolved=args.ambiguity_resolved,
            )
        )
    if command == "record-delivery":
        return _transition(
            coordinator.record_delivery(
                **fence,
                message_id=args.message_id,
                delivery_status=args.delivery_status,
                delivery_evidence_verified=args.delivery_evidence_verified,
            )
        )
    if command == "complete-control-turn":
        return _transition(
            coordinator.complete_control_turn(
                **fence,
                source_thread_id=args.source_thread_id,
                task_inactive=args.task_inactive,
            )
        )
    if command == "requeue-known-nondelivery":
        return _transition(
            coordinator.requeue_known_nondelivery(
                **fence,
                nondelivery_verified=args.nondelivery_verified,
                task_inactive=args.task_inactive,
            )
        )
    if command == "accept-attestation":
        return _transition(
            coordinator.accept_attestation(
                **fence,
                source_thread_id=args.source_thread_id,
                packet_text=_read_input(args.packet_file),
                task_inactive=args.task_inactive,
            )
        )
    if command == "plan-assignment":
        spec = strict_json_loads(_read_input(args.spec_file))
        return _transition(coordinator.plan_assignment(**fence, spec=spec))
    if command == "accept-result":
        return _transition(
            coordinator.accept_result(
                **fence,
                source_thread_id=args.source_thread_id,
                packet_text=_read_input(args.packet_file),
                task_inactive=args.task_inactive,
            )
        )
    if command == "accept-protocol-reject":
        return _transition(
            coordinator.accept_protocol_reject(
                **fence,
                source_thread_id=args.source_thread_id,
                packet_text=_read_input(args.packet_file),
                task_inactive=args.task_inactive,
            )
        )
    if command == "commit-result":
        return _transition(
            coordinator.commit_result(
                **fence,
                disposition=args.disposition,
                artifacts_reviewed=args.artifacts_reviewed,
            )
        )
    if command == "plan-ack":
        return _transition(coordinator.plan_ack(**fence))
    if command == "plan-stop":
        return _transition(coordinator.plan_stop(**fence, reason_code=args.reason_code))
    if command == "accept-stop-ack":
        return _transition(
            coordinator.accept_stop_ack(
                **fence,
                source_thread_id=args.source_thread_id,
                packet_text=_read_input(args.packet_file),
                task_inactive=args.task_inactive,
            )
        )
    if command == "record-archived":
        return _transition(
            coordinator.record_archived(
                **fence,
                delivery_status=args.delivery_status,
                delivery_evidence_verified=args.delivery_evidence_verified,
            )
        )
    raise OrchestratorError("COMMAND_UNSUPPORTED", "The requested command is unsupported.")


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        _emit(dispatch(args))
        return 0
    except OrchestratorError as exc:
        _emit({"ok": False, "error": exc.as_dict()})
        return 2
    except Exception:
        _emit(
            {
                "ok": False,
                "error": {
                    "code": "INTERNAL_ERROR",
                    "message": "The orchestrator encountered an unexpected local error; no raw input or path was emitted.",
                },
            }
        )
        return 3


if __name__ == "__main__":
    raise SystemExit(main())

"""Stateful, fail-closed broker behind the Project Glacier MCP tools."""

from __future__ import annotations

import secrets
import threading
from typing import Any

from .errors import (
    AuthenticationError,
    DeliveryUnknownError,
    GlacierError,
    InvalidStateError,
    RemoteRejectedError,
    ValidationError,
)
from .journal import OperationJournal
from .models import (
    CaseRecord,
    CaseState,
    Category,
    DeliveryStatus,
    OperationRecord,
    OperationStatus,
    Outcome,
    PendingResult,
    PreparedSubmission,
    TranscriptTurn,
    sha256_json,
    utc_now,
    validate_bounded_text,
    validate_category,
    validate_identifier,
)
from .settings import Settings
from .transport import ChallengeTransport, RequestsChallengeTransport

_VISUAL_SIGNAL_STATE = {
    "pink_warning": "NOT_OBSERVABLE_VIA_API",
    "message_field_available": "NOT_OBSERVABLE_VIA_API",
    "rendered_detection_indicator": "NOT_OBSERVABLE_VIA_API",
}
_UNKNOWN_STATES = {
    CaseState.CHAT_DELIVERY_UNKNOWN,
    CaseState.CLEAR_DELIVERY_UNKNOWN,
    CaseState.RECOVERY_REQUIRED,
}


class GlacierBroker:
    """Own exactly one authenticated challenge conversation at a time."""

    def __init__(
        self,
        settings: Settings,
        *,
        transport: ChallengeTransport | None = None,
        journal: OperationJournal | None = None,
    ) -> None:
        self._settings = settings
        self._transport = (
            RequestsChallengeTransport(settings) if transport is None else transport
        )
        self._journal = OperationJournal(settings.state_dir) if journal is None else journal
        self._lock = threading.RLock()
        self._cases: dict[str, CaseRecord] = {}
        self._active_case_id: str | None = None
        self._result_cache: dict[str, dict[str, Any]] = {}
        self._schema_hash = ""
        self._load_recovery_case()

    def close(self) -> None:
        self._journal.close()

    @property
    def current_case_state(self) -> str | None:
        if self._active_case_id is None:
            return None
        case = self._cases.get(self._active_case_id)
        return case.state.value if case else None

    @property
    def active_case_id(self) -> str | None:
        return self._active_case_id

    def _load_recovery_case(self) -> None:
        unfinished = self._journal.unfinished_cases()
        if not unfinished:
            return
        metadata = unfinished[0]
        persisted_state = CaseState(metadata["state"])
        recovered_state = (
            persisted_state
            if persisted_state
            in {
                CaseState.CHAT_DELIVERY_UNKNOWN,
                CaseState.CLEAR_DELIVERY_UNKNOWN,
                CaseState.SUBMISSION_DELIVERY_UNKNOWN,
                CaseState.RECOVERY_REQUIRED,
            }
            else CaseState.RECOVERY_REQUIRED
        )
        case = CaseRecord(
            case_id=metadata["case_id"],
            experiment_id=metadata["experiment_id"],
            category=metadata["category"],
            policy_subcategory=metadata["policy_subcategory"],
            state=recovered_state,
            created_at=metadata["created_at"],
            updated_at=utc_now(),
            recovery_note=(
                "The prior MCP process ended before this case reached a confirmed closed state. "
                "No transcript or authentication material was restored from disk."
            ),
            recovery_transcript_hash=metadata["transcript_hash"],
            evaluation_id=metadata["evaluation_id"],
            conversation_id=metadata["conversation_id"],
        )
        self._cases[case.case_id] = case
        self._active_case_id = case.case_id
        self._journal.save_case(case)

    def _require_authenticated(self) -> None:
        if not self._transport.authenticated:
            raise AuthenticationError(
                "AUTH_MISSING",
                "The challenge session is not open.",
                next_action="Call glacier_open_session before using case tools.",
            )

    def _require_case(self, case_id: str) -> CaseRecord:
        validate_identifier(case_id, field_name="case_id")
        case = self._cases.get(case_id)
        if case is None:
            raise ValidationError(
                "INVALID_ARGUMENT",
                "The case ID is not known to this MCP process.",
            )
        return case

    def _active_case(self) -> CaseRecord | None:
        if self._active_case_id is None:
            return None
        return self._cases.get(self._active_case_id)

    def _case_result(self, case: CaseRecord) -> dict[str, Any]:
        return {
            **case.public_dict(),
            "visual_signals": dict(_VISUAL_SIGNAL_STATE),
        }

    def _operation_request(
        self,
        *,
        operation_id: str,
        tool_name: str,
        case: CaseRecord,
        request_value: dict[str, Any],
    ) -> OperationRecord | None:
        validate_identifier(operation_id, field_name="operation_id")
        return self._journal.begin_operation(
            operation_id=operation_id,
            tool_name=tool_name,
            case_id=case.case_id,
            request_hash=sha256_json(request_value),
        )

    def _existing_operation_for_request(
        self,
        *,
        operation_id: str,
        tool_name: str,
        case: CaseRecord,
        request_value: dict[str, Any],
    ) -> dict[str, Any] | None:
        validate_identifier(operation_id, field_name="operation_id")
        if self._journal.get_operation(operation_id) is None:
            return None
        existing = self._operation_request(
            operation_id=operation_id,
            tool_name=tool_name,
            case=case,
            request_value=request_value,
        )
        if existing is None:
            raise InvalidStateError(
                "RECOVERY_REQUIRED",
                "The operation journal changed unexpectedly.",
                next_action="Stop live writes and inspect the local journal.",
            )
        return self._existing_operation_result(existing)

    def _existing_operation_result(
        self,
        operation: OperationRecord,
    ) -> dict[str, Any]:
        if operation.status is OperationStatus.ACKNOWLEDGED:
            cached = self._result_cache.get(operation.operation_id)
            if cached is not None:
                return {**cached, "duplicate_operation": True}
            return {
                "ok": True,
                "duplicate_operation": True,
                "operation_id": operation.operation_id,
                "delivery_status": DeliveryStatus.ACKNOWLEDGED.value,
                "result_metadata": operation.result_metadata,
                "result_body_available": False,
            }
        if operation.status is OperationStatus.REJECTED:
            raise InvalidStateError(
                "OPERATION_ALREADY_REJECTED",
                "This operation ID already received a definitive rejection.",
                delivery_status=DeliveryStatus.REJECTED.value,
                next_action="Correct the request and use a new operation ID.",
            )
        raise DeliveryUnknownError(
            "DELIVERY_UNKNOWN",
            "This operation ID has an unresolved delivery outcome.",
            delivery_status=DeliveryStatus.UNKNOWN.value,
            next_action="Do not retry this operation ID or send another mutation.",
        )

    def _save_case(self, case: CaseRecord) -> None:
        case.updated_at = utc_now()
        self._journal.save_case(case)

    def _require_no_unresolved_operation(self) -> None:
        unresolved = self._journal.first_unresolved_operation()
        if unresolved is None:
            return
        raise DeliveryUnknownError(
            "DELIVERY_UNKNOWN",
            "A prior state-changing operation still has an unresolved delivery outcome.",
            delivery_status=DeliveryStatus.UNKNOWN.value,
            next_action="Do not send another mutation; reconcile the recorded operation first.",
        )

    def _mark_delivery_unknown(
        self,
        *,
        case: CaseRecord,
        operation_id: str,
        state: CaseState,
        recovery_note: str,
    ) -> None:
        case.state = state
        case.last_delivery_status = DeliveryStatus.UNKNOWN
        case.recovery_note = recovery_note
        try:
            self._save_case(case)
        except Exception:  # noqa: BLE001 - preserve the original fail-closed outcome
            pass
        try:
            self._journal.finish_operation(
                operation_id,
                status=OperationStatus.UNKNOWN,
                result_metadata={"case_state": case.state.value},
            )
        except Exception:  # noqa: BLE001 - persisted IN_FLIGHT is unresolved on restart
            pass

    @staticmethod
    def _is_definitive_rejection(error: RemoteRejectedError) -> bool:
        return error.http_status is not None and 400 <= error.http_status < 500

    def _recover_pending(self, pending: PendingResult) -> CaseRecord:
        case = self._active_case()
        prior_state: CaseState | None = case.state if case is not None else None
        if case is None:
            now = utc_now()
            case = CaseRecord(
                case_id=f"case_{secrets.token_urlsafe(18)}",
                experiment_id=f"recovered-{now.replace(':', '').replace('-', '')}",
                category=pending.category,
                policy_subcategory=pending.policy_subcategory,
                state=CaseState.PENDING_EVALUATION,
                created_at=now,
                updated_at=now,
            )
            self._cases[case.case_id] = case
            self._active_case_id = case.case_id
        elif (
            case.state is not CaseState.RECOVERY_REQUIRED
            and (
                case.category != pending.category
                or case.policy_subcategory != pending.policy_subcategory
            )
        ):
            case.state = CaseState.RECOVERY_REQUIRED
            case.recovery_note = (
                "The remote pending draft does not match the active case category."
            )
            self._save_case(case)
            raise InvalidStateError(
                "RECOVERY_REQUIRED",
                "The pending evaluation does not match the active local case.",
                next_action="Stop and reconcile the pending evaluation manually.",
            )
        else:
            case.category = pending.category
            case.policy_subcategory = pending.policy_subcategory

        case.transcript = list(pending.transcript)
        case.recovery_transcript_hash = ""
        event_types = {event.event_type for event in case.action_events}
        if "dialog_stopped_by_guardrail" not in event_types:
            case.add_event(
                "dialog_stopped_by_guardrail",
                reply=pending.stop_reason,
                outcome_locked=Outcome.BLOCKED.value,
            )
        if not event_types or prior_state is not CaseState.PENDING_EVALUATION:
            case.add_event(
                "pending_evaluation_restored",
                restored_count=len(case.transcript),
            )
        case.prepared_submission = None
        chat_reconciled = self._reconcile_pending_chat_operation(case)
        if prior_state in {
            CaseState.CLEAR_DELIVERY_UNKNOWN,
            CaseState.SUBMISSION_DELIVERY_UNKNOWN,
        }:
            case.state = prior_state
            case.last_delivery_status = DeliveryStatus.UNKNOWN
            case.recovery_note = (
                "A server pending draft was observed, but the prior mutation remains "
                "delivery-unknown and read-only."
            )
        elif prior_state is CaseState.CHAT_DELIVERY_UNKNOWN and not chat_reconciled:
            case.state = CaseState.CHAT_DELIVERY_UNKNOWN
            case.last_delivery_status = DeliveryStatus.UNKNOWN
            case.recovery_note = (
                "The pending draft did not match the unknown chat operation; manual "
                "reconciliation is required."
            )
        else:
            case.state = CaseState.PENDING_EVALUATION
            case.last_delivery_status = DeliveryStatus.ACKNOWLEDGED
            case.recovery_note = (
                "Recovered from the server-authoritative pending draft"
                + (
                    " and reconciled the matching chat delivery."
                    if chat_reconciled
                    else "."
                )
            )
        self._save_case(case)
        return case

    def _reconcile_pending_chat_operation(self, case: CaseRecord) -> bool:
        unresolved = self._journal.first_unresolved_operation()
        if (
            unresolved is None
            or unresolved.tool_name != "glacier_send_message"
            or unresolved.case_id != case.case_id
        ):
            return False
        last_user_turn = next(
            (turn for turn in reversed(case.transcript) if turn.role == "user"),
            None,
        )
        if last_user_turn is None:
            return False
        expected_hash = sha256_json(
            {
                "case_id": case.case_id,
                "category": case.category,
                "policy_subcategory": case.policy_subcategory,
                "message": last_user_turn.text,
            }
        )
        if expected_hash != unresolved.request_hash:
            return False
        self._journal.finish_operation(
            unresolved.operation_id,
            status=OperationStatus.ACKNOWLEDGED,
            result_metadata={
                "case_state": CaseState.PENDING_EVALUATION.value,
                "transcript_hash": case.transcript_hash(),
                "pending_required": True,
                "reconciled_from_pending": True,
            },
        )
        return True

    def open_session(self) -> dict[str, Any]:
        with self._lock:
            self._transport.authenticate()
            schema = self._transport.get_openapi()
            self._schema_hash = sha256_json(schema)
            pending = self._transport.get_pending()
            case = self._recover_pending(pending) if pending.pending else self._active_case()
            if (
                case is not None
                and case.state is CaseState.PENDING_EVALUATION
                and not pending.pending
            ):
                case.state = CaseState.RECOVERY_REQUIRED
                case.recovery_note = (
                    "Local metadata expected a pending evaluation, but the server did not "
                    "report one."
                )
                self._save_case(case)
            return {
                "ok": True,
                "authenticated": True,
                "schema_hash": self._schema_hash,
                "pending_evaluation": pending.pending,
                "active_case": self._case_result(case) if case else None,
                "submission_enabled": self._settings.allow_submit,
                "submission_count": self._journal.submission_count(),
                "submission_limit": self._settings.max_submissions,
                "visual_signal_support": dict(_VISUAL_SIGNAL_STATE),
            }

    def start_case(
        self,
        experiment_id: str,
        category: str,
        policy_subcategory: str = "",
    ) -> dict[str, Any]:
        with self._lock:
            self._require_authenticated()
            validate_identifier(experiment_id, field_name="experiment_id")
            parsed_category, parsed_subcategory = validate_category(
                category,
                policy_subcategory,
            )
            self._require_no_unresolved_operation()
            active = self._active_case()
            if active is not None and active.state is not CaseState.CLOSED:
                raise InvalidStateError(
                    "CASE_CONFLICT",
                    "Another challenge case is still active.",
                    next_action="Finish or safely reset the active case first.",
                )
            pending = self._transport.get_pending()
            if pending.pending:
                recovered = self._recover_pending(pending)
                raise InvalidStateError(
                    "PENDING_REQUIRED",
                    "A pending evaluation must be adjudicated before a new case starts.",
                    next_action=f"Use recovered case {recovered.case_id}.",
                )

            now = utc_now()
            case = CaseRecord(
                case_id=f"case_{secrets.token_urlsafe(18)}",
                experiment_id=experiment_id,
                category=parsed_category.value,
                policy_subcategory=parsed_subcategory,
                state=CaseState.ACTIVE,
                created_at=now,
                updated_at=now,
            )
            case.add_event("evaluation_page_loaded")
            self._cases[case.case_id] = case
            self._active_case_id = case.case_id
            self._save_case(case)
            return {"ok": True, "case": self._case_result(case)}

    def send_message(
        self,
        case_id: str,
        message: str,
        operation_id: str,
    ) -> dict[str, Any]:
        with self._lock:
            self._require_authenticated()
            case = self._require_case(case_id)
            message = validate_bounded_text(
                message,
                field_name="message",
                maximum_chars=self._settings.max_message_chars,
            )
            request_value = {
                "case_id": case.case_id,
                "category": case.category,
                "policy_subcategory": case.policy_subcategory,
                "message": message,
            }
            duplicate = self._existing_operation_for_request(
                operation_id=operation_id,
                tool_name="glacier_send_message",
                case=case,
                request_value=request_value,
            )
            if duplicate is not None:
                return duplicate
            self._require_no_unresolved_operation()
            if case.state is not CaseState.ACTIVE:
                raise InvalidStateError(
                    "INVALID_STATE",
                    "Messages are only allowed while the case is ACTIVE.",
                    next_action="Adjudicate, reconcile, or reset the current case state.",
                )
            user_turn_count = sum(turn.role == "user" for turn in case.transcript)
            if user_turn_count >= self._settings.max_turns:
                raise InvalidStateError(
                    "INVALID_STATE",
                    "The configured per-case turn limit has been reached.",
                    next_action="Adjudicate the case instead of extending the conversation.",
                )
            existing = self._operation_request(
                operation_id=operation_id,
                tool_name="glacier_send_message",
                case=case,
                request_value=request_value,
            )
            if existing is not None:
                return self._existing_operation_result(existing)

            original_turn_count = len(case.transcript)
            original_event_count = len(case.action_events)
            try:
                case.add_event("send_message", message=message)
                case.transcript.append(
                    TranscriptTurn(
                        role="user",
                        text=message,
                        category=case.category,
                        policy_subcategory=case.policy_subcategory,
                    )
                )
                self._save_case(case)
                remote = self._transport.chat(
                    category=case.category,
                    policy_subcategory=case.policy_subcategory,
                    message=message,
                )
                if remote.transcript is not None:
                    case.transcript = list(remote.transcript)
                else:
                    case.transcript.append(
                        TranscriptTurn(
                            role="assistant",
                            text=remote.reply,
                            category=case.category,
                            policy_subcategory=case.policy_subcategory,
                        )
                    )
                case.add_event(
                    "receive_genai_response",
                    reply=remote.reply,
                    ok=remote.http_status < 400,
                )
                if remote.pending_required:
                    case.state = CaseState.PENDING_EVALUATION
                    case.add_event(
                        "dialog_stopped_by_guardrail",
                        reply=remote.stop_reason or remote.reply,
                        outcome_locked=Outcome.BLOCKED.value,
                    )
                case.last_delivery_status = DeliveryStatus.ACKNOWLEDGED
                self._save_case(case)
                result = {
                    "ok": True,
                    "operation_id": operation_id,
                    "delivery_status": DeliveryStatus.ACKNOWLEDGED.value,
                    "reply": remote.reply,
                    "pending_required": remote.pending_required,
                    "case": self._case_result(case),
                }
                self._journal.finish_operation(
                    operation_id,
                    status=OperationStatus.ACKNOWLEDGED,
                    result_metadata={
                        "case_state": case.state.value,
                        "transcript_hash": case.transcript_hash(),
                        "pending_required": remote.pending_required,
                    },
                )
            except RemoteRejectedError as error:
                if not self._is_definitive_rejection(error):
                    self._mark_delivery_unknown(
                        case=case,
                        operation_id=operation_id,
                        state=CaseState.CHAT_DELIVERY_UNKNOWN,
                        recovery_note=(
                            "The chat POST may have been processed. It must not be retried."
                        ),
                    )
                    raise DeliveryUnknownError(
                        "DELIVERY_UNKNOWN",
                        "The chat request has an unknown delivery outcome.",
                        delivery_status=DeliveryStatus.UNKNOWN.value,
                        next_action="Call glacier_get_state; do not send another message.",
                    ) from error
                del case.transcript[original_turn_count:]
                del case.action_events[original_event_count:]
                case.last_delivery_status = DeliveryStatus.REJECTED
                try:
                    self._save_case(case)
                    self._journal.finish_operation(
                        operation_id,
                        status=OperationStatus.REJECTED,
                        result_metadata={"case_state": case.state.value},
                    )
                except Exception as persist_error:  # noqa: BLE001
                    self._mark_delivery_unknown(
                        case=case,
                        operation_id=operation_id,
                        state=CaseState.CHAT_DELIVERY_UNKNOWN,
                        recovery_note="The rejected chat could not be journaled safely.",
                    )
                    raise DeliveryUnknownError(
                        "DELIVERY_UNKNOWN",
                        "The chat operation could not be finalized safely.",
                        delivery_status=DeliveryStatus.UNKNOWN.value,
                        next_action="Do not send another mutation; inspect local state.",
                    ) from persist_error
                raise
            except Exception as error:  # noqa: BLE001 - every post-intent failure is ambiguous
                self._mark_delivery_unknown(
                    case=case,
                    operation_id=operation_id,
                    state=CaseState.CHAT_DELIVERY_UNKNOWN,
                    recovery_note=(
                        "The chat POST may have been processed. It must not be retried."
                    ),
                )
                raise DeliveryUnknownError(
                    "DELIVERY_UNKNOWN",
                    "The chat request has an unknown delivery outcome.",
                    delivery_status=DeliveryStatus.UNKNOWN.value,
                    next_action="Call glacier_get_state; do not send another message.",
                ) from error
            self._result_cache[operation_id] = result
            return result

    def get_state(
        self,
        case_id: str | None = None,
        *,
        sync_remote: bool = True,
    ) -> dict[str, Any]:
        with self._lock:
            self._require_authenticated()
            case = self._require_case(case_id) if case_id else self._active_case()
            pending_value: bool | None = None
            if sync_remote:
                pending = self._transport.get_pending()
                pending_value = pending.pending
                if pending.pending:
                    case = self._recover_pending(pending)
                elif case is not None and case.state is CaseState.PENDING_EVALUATION:
                    case.state = CaseState.RECOVERY_REQUIRED
                    case.recovery_note = (
                        "The local case was pending, but the server no longer reports a "
                        "pending draft."
                    )
                    self._save_case(case)
            return {
                "ok": True,
                "authenticated": True,
                "schema_hash": self._schema_hash,
                "pending_evaluation": pending_value,
                "case": self._case_result(case) if case else None,
            }

    def prepare_submission(
        self,
        case_id: str,
        outcome: str,
        case_summary: str,
    ) -> dict[str, Any]:
        with self._lock:
            self._require_authenticated()
            case = self._require_case(case_id)
            self._require_no_unresolved_operation()
            if case.state not in {
                CaseState.ACTIVE,
                CaseState.PENDING_EVALUATION,
                CaseState.PREPARED,
            }:
                raise InvalidStateError(
                    "INVALID_STATE",
                    "This case cannot be prepared for submission in its current state.",
                )
            if not any(turn.role == "user" for turn in case.transcript):
                raise InvalidStateError(
                    "INVALID_STATE",
                    "At least one delivered user message is required before submission.",
                )
            try:
                parsed_outcome = Outcome(outcome)
            except ValueError as exc:
                raise ValidationError(
                    "INVALID_ARGUMENT",
                    "The outcome must be Attack Succeeded or Attack Blocked.",
                ) from exc
            summary = validate_bounded_text(
                case_summary,
                field_name="case_summary",
                maximum_chars=self._settings.max_summary_chars,
            ).strip()

            if case.state is not CaseState.PREPARED:
                case.add_event(
                    "submit_evaluation_click",
                    transcriptLength=len(case.transcript),
                )
            payload = {
                "category": case.category,
                "policy_subcategory": case.policy_subcategory,
                "experience": parsed_outcome.value,
                "feedback_labels": [],
                "comment": summary,
                "transcript": [turn.to_api_dict() for turn in case.transcript],
                "action_events": [event.to_api_dict() for event in case.action_events],
            }
            prepared = PreparedSubmission(
                payload=payload,
                payload_hash=sha256_json(payload),
                prepared_at=utc_now(),
            )
            case.prepared_submission = prepared
            case.state = CaseState.PREPARED
            self._save_case(case)
            return {
                "ok": True,
                "case_id": case.case_id,
                "payload_hash": prepared.payload_hash,
                "prepared_at": prepared.prepared_at,
                "preview": payload,
                "network_mutation_performed": False,
            }

    def commit_submission(
        self,
        case_id: str,
        payload_hash: str,
        operation_id: str,
    ) -> dict[str, Any]:
        with self._lock:
            self._require_authenticated()
            case = self._require_case(case_id)
            request_value = {
                "case_id": case.case_id,
                "payload_hash": payload_hash,
            }
            duplicate = self._existing_operation_for_request(
                operation_id=operation_id,
                tool_name="glacier_commit_submission",
                case=case,
                request_value=request_value,
            )
            if duplicate is not None:
                return duplicate
            self._require_no_unresolved_operation()
            if case.prepared_submission is None:
                raise InvalidStateError(
                    "INVALID_STATE",
                    "The case does not have a current prepared submission.",
                    next_action="Call glacier_prepare_submission and review its preview first.",
                )
            if payload_hash != case.prepared_submission.payload_hash:
                raise ValidationError(
                    "HASH_MISMATCH",
                    "The submitted payload hash does not match the current immutable preview.",
                    next_action="Use the exact hash returned by glacier_prepare_submission.",
                )
            if case.state is not CaseState.PREPARED:
                raise InvalidStateError(
                    "INVALID_STATE",
                    "The case is not in the PREPARED state.",
                )
            if not self._settings.allow_submit:
                raise InvalidStateError(
                    "SUBMISSION_DISABLED",
                    "Live evaluation submission is disabled in broker configuration.",
                    next_action=(
                        "Enable GLACIER_ALLOW_SUBMIT outside the model prompt after testing."
                    ),
                )
            if self._journal.submission_count() >= self._settings.max_submissions:
                raise InvalidStateError(
                    "SUBMISSION_LIMIT_REACHED",
                    "The configured submission quota has been reached.",
                    next_action="Stop autonomous submission and review the configured limit.",
                )

            existing = self._operation_request(
                operation_id=operation_id,
                tool_name="glacier_commit_submission",
                case=case,
                request_value=request_value,
            )
            if existing is not None:
                return self._existing_operation_result(existing)

            try:
                receipt = self._transport.evaluate(case.prepared_submission.payload)
                case.evaluation_id = receipt.evaluation_id
                case.conversation_id = receipt.conversation_id
                case.state = CaseState.SUBMITTED
                case.last_delivery_status = DeliveryStatus.ACKNOWLEDGED
                self._save_case(case)
                result = {
                    "ok": True,
                    "operation_id": operation_id,
                    "delivery_status": DeliveryStatus.ACKNOWLEDGED.value,
                    "submitted": True,
                    "message": receipt.message,
                    "evaluation_id": receipt.evaluation_id,
                    "conversation_id": receipt.conversation_id,
                    "case": self._case_result(case),
                }
                self._journal.finish_operation(
                    operation_id,
                    status=OperationStatus.ACKNOWLEDGED,
                    result_metadata={
                        "case_state": case.state.value,
                        "evaluation_id": receipt.evaluation_id,
                        "conversation_id": receipt.conversation_id,
                    },
                )
            except RemoteRejectedError as error:
                if not self._is_definitive_rejection(error):
                    self._mark_delivery_unknown(
                        case=case,
                        operation_id=operation_id,
                        state=CaseState.SUBMISSION_DELIVERY_UNKNOWN,
                        recovery_note=(
                            "The evaluation POST may have committed. It must not be retried."
                        ),
                    )
                    raise DeliveryUnknownError(
                        "DELIVERY_UNKNOWN",
                        "The evaluation submission has an unknown delivery outcome.",
                        delivery_status=DeliveryStatus.UNKNOWN.value,
                        next_action="Do not retry. Reconcile the submission manually.",
                    ) from error
                case.last_delivery_status = DeliveryStatus.REJECTED
                try:
                    self._save_case(case)
                    self._journal.finish_operation(
                        operation_id,
                        status=OperationStatus.REJECTED,
                        result_metadata={"case_state": case.state.value},
                    )
                except Exception as persist_error:  # noqa: BLE001
                    self._mark_delivery_unknown(
                        case=case,
                        operation_id=operation_id,
                        state=CaseState.SUBMISSION_DELIVERY_UNKNOWN,
                        recovery_note="The rejected evaluation could not be journaled safely.",
                    )
                    raise DeliveryUnknownError(
                        "DELIVERY_UNKNOWN",
                        "The evaluation operation could not be finalized safely.",
                        delivery_status=DeliveryStatus.UNKNOWN.value,
                        next_action="Do not retry. Reconcile the submission manually.",
                    ) from persist_error
                raise
            except Exception as error:  # noqa: BLE001 - every post-intent failure is ambiguous
                self._mark_delivery_unknown(
                    case=case,
                    operation_id=operation_id,
                    state=CaseState.SUBMISSION_DELIVERY_UNKNOWN,
                    recovery_note=(
                        "The evaluation POST may have committed. It must never be retried "
                        "or cleared automatically."
                    ),
                )
                raise DeliveryUnknownError(
                    "DELIVERY_UNKNOWN",
                    "The evaluation submission has an unknown delivery outcome.",
                    delivery_status=DeliveryStatus.UNKNOWN.value,
                    next_action="Do not retry. Reconcile the submission manually.",
                ) from error
            self._result_cache[operation_id] = result
            return result

    def reset_case(
        self,
        case_id: str,
        transcript_hash: str,
        operation_id: str,
    ) -> dict[str, Any]:
        with self._lock:
            self._require_authenticated()
            case = self._require_case(case_id)
            if transcript_hash != case.transcript_hash():
                raise ValidationError(
                    "HASH_MISMATCH",
                    "The transcript hash does not match the current case.",
                    next_action="Refresh state and reset only the intended conversation.",
                )
            request_value = {
                "case_id": case.case_id,
                "transcript_hash": transcript_hash,
            }
            duplicate = self._existing_operation_for_request(
                operation_id=operation_id,
                tool_name="glacier_reset_case",
                case=case,
                request_value=request_value,
            )
            if duplicate is not None:
                return duplicate
            if case.state in {CaseState.PENDING_EVALUATION, CaseState.PREPARED}:
                raise InvalidStateError(
                    "INVALID_STATE",
                    "A pending or prepared terminal case cannot be cleared.",
                    next_action="Complete the honest evaluation submission first.",
                )
            if case.state is CaseState.SUBMISSION_DELIVERY_UNKNOWN:
                raise InvalidStateError(
                    "RECOVERY_REQUIRED",
                    "An evaluation may already have been submitted and cannot be reset safely.",
                    next_action="Do not retry or clear it; reconcile the leaderboard manually.",
                )
            if case.state in _UNKNOWN_STATES:
                raise InvalidStateError(
                    "RECOVERY_REQUIRED",
                    "An unresolved case is read-only and cannot be reset automatically.",
                    next_action="Record INTERFACE_OR_TEST_FAILURE and reconcile it manually.",
                )
            self._require_no_unresolved_operation()

            pending = self._transport.get_pending()
            if pending.pending:
                recovered = self._recover_pending(pending)
                raise InvalidStateError(
                    "PENDING_REQUIRED",
                    "The server reports a pending evaluation; reset is forbidden.",
                    next_action=f"Adjudicate recovered case {recovered.case_id}.",
                )

            existing = self._operation_request(
                operation_id=operation_id,
                tool_name="glacier_reset_case",
                case=case,
                request_value=request_value,
            )
            if existing is not None:
                return self._existing_operation_result(existing)

            try:
                if case.state is not CaseState.SUBMITTED:
                    self._transport.clear()
                case.state = CaseState.CLOSED
                case.last_delivery_status = DeliveryStatus.ACKNOWLEDGED
                case.recovery_note = ""
                self._save_case(case)
                result = {
                    "ok": True,
                    "operation_id": operation_id,
                    "delivery_status": DeliveryStatus.ACKNOWLEDGED.value,
                    "cleared": True,
                    "case": self._case_result(case),
                }
                self._journal.finish_operation(
                    operation_id,
                    status=OperationStatus.ACKNOWLEDGED,
                    result_metadata={"case_state": case.state.value},
                )
            except RemoteRejectedError as error:
                if not self._is_definitive_rejection(error):
                    self._mark_delivery_unknown(
                        case=case,
                        operation_id=operation_id,
                        state=CaseState.CLEAR_DELIVERY_UNKNOWN,
                        recovery_note=(
                            "The clear-chat POST may have executed. It must not be retried."
                        ),
                    )
                    raise DeliveryUnknownError(
                        "DELIVERY_UNKNOWN",
                        "The reset request has an unknown delivery outcome.",
                        delivery_status=DeliveryStatus.UNKNOWN.value,
                        next_action="Do not retry. Reconcile the challenge state first.",
                    ) from error
                case.last_delivery_status = DeliveryStatus.REJECTED
                try:
                    self._save_case(case)
                    self._journal.finish_operation(
                        operation_id,
                        status=OperationStatus.REJECTED,
                        result_metadata={"case_state": case.state.value},
                    )
                except Exception as persist_error:  # noqa: BLE001
                    self._mark_delivery_unknown(
                        case=case,
                        operation_id=operation_id,
                        state=CaseState.CLEAR_DELIVERY_UNKNOWN,
                        recovery_note="The rejected reset could not be journaled safely.",
                    )
                    raise DeliveryUnknownError(
                        "DELIVERY_UNKNOWN",
                        "The reset operation could not be finalized safely.",
                        delivery_status=DeliveryStatus.UNKNOWN.value,
                        next_action="Do not retry. Reconcile the challenge state first.",
                    ) from persist_error
                raise
            except Exception as error:  # noqa: BLE001 - every post-intent failure is ambiguous
                self._mark_delivery_unknown(
                    case=case,
                    operation_id=operation_id,
                    state=CaseState.CLEAR_DELIVERY_UNKNOWN,
                    recovery_note=(
                        "The clear-chat POST may have executed. It must not be retried blindly."
                    ),
                )
                raise DeliveryUnknownError(
                    "DELIVERY_UNKNOWN",
                    "The reset request has an unknown delivery outcome.",
                    delivery_status=DeliveryStatus.UNKNOWN.value,
                    next_action="Do not retry. Reconcile the challenge state first.",
                ) from error
            self._active_case_id = None
            self._result_cache[operation_id] = result
            return result

    def error_result(self, error: GlacierError) -> dict[str, Any]:
        return {
            "ok": False,
            "error": error.as_dict(case_state=self.current_case_state),
            "active_case_id": self._active_case_id,
        }

    @staticmethod
    def categories() -> dict[str, list[str]]:
        return {
            Category.CONTENT_MODERATION.value: [],
            Category.PROMPT_INJECTION.value: [],
            Category.PII_DETECTION.value: [],
            Category.BUSINESS_RULES.value: [
                "Prevent Destructive Actions",
                "IAM and Authorization",
                "Secrets and Credentials",
                "Internal System Exposure",
                "Infrastructure Exposure",
                "Other",
            ],
        }

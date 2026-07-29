"""Typed challenge and broker models."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from .errors import ValidationError

_IDENTIFIER = re.compile(r"\A[A-Za-z0-9][A-Za-z0-9._:-]{0,127}\Z")


class Category(StrEnum):
    CONTENT_MODERATION = "Content Moderation"
    PROMPT_INJECTION = "Prompt Injection (PI) detection"
    PII_DETECTION = "Personally Identifiable Information (PII) detection"
    BUSINESS_RULES = "Business Rules Verifier"


class PolicySubcategory(StrEnum):
    PREVENT_DESTRUCTIVE_ACTIONS = "Prevent Destructive Actions"
    IAM_AND_AUTHORIZATION = "IAM and Authorization"
    SECRETS_AND_CREDENTIALS = "Secrets and Credentials"
    INTERNAL_SYSTEM_EXPOSURE = "Internal System Exposure"
    INFRASTRUCTURE_EXPOSURE = "Infrastructure Exposure"
    OTHER = "Other"


class Outcome(StrEnum):
    SUCCEEDED = "Attack Succeeded"
    BLOCKED = "Attack Blocked"


class CaseState(StrEnum):
    ACTIVE = "ACTIVE"
    PENDING_EVALUATION = "PENDING_EVALUATION"
    PREPARED = "PREPARED"
    SUBMITTED = "SUBMITTED"
    CLOSED = "CLOSED"
    CHAT_DELIVERY_UNKNOWN = "CHAT_DELIVERY_UNKNOWN"
    CLEAR_DELIVERY_UNKNOWN = "CLEAR_DELIVERY_UNKNOWN"
    SUBMISSION_DELIVERY_UNKNOWN = "SUBMISSION_DELIVERY_UNKNOWN"
    RECOVERY_REQUIRED = "RECOVERY_REQUIRED"


class DeliveryStatus(StrEnum):
    NOT_SENT = "NOT_SENT"
    ACKNOWLEDGED = "ACKNOWLEDGED"
    REJECTED = "REJECTED"
    UNKNOWN = "UNKNOWN"


class OperationStatus(StrEnum):
    IN_FLIGHT = "IN_FLIGHT"
    ACKNOWLEDGED = "ACKNOWLEDGED"
    REJECTED = "REJECTED"
    UNKNOWN = "UNKNOWN"


def utc_now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def sha256_json(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def validate_identifier(value: str, *, field_name: str) -> str:
    if not _IDENTIFIER.fullmatch(value):
        raise ValidationError(
            "INVALID_ARGUMENT",
            f"{field_name} must contain 1-128 allowlisted identifier characters.",
        )
    return value


def validate_category(
    category: str,
    policy_subcategory: str | None,
) -> tuple[Category, str]:
    try:
        parsed_category = Category(category)
    except ValueError as exc:
        raise ValidationError(
            "INVALID_ARGUMENT",
            "The category is not one of the exact challenge categories.",
        ) from exc

    subcategory = (policy_subcategory or "").strip()
    if parsed_category is Category.BUSINESS_RULES:
        try:
            parsed_subcategory = PolicySubcategory(subcategory)
        except ValueError as exc:
            raise ValidationError(
                "INVALID_ARGUMENT",
                "Business Rules Verifier requires an exact policy subcategory.",
            ) from exc
        return parsed_category, parsed_subcategory.value

    if subcategory:
        raise ValidationError(
            "INVALID_ARGUMENT",
            "A policy subcategory is only valid for Business Rules Verifier.",
        )
    return parsed_category, ""


def validate_bounded_text(
    value: str,
    *,
    field_name: str,
    maximum_chars: int,
) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValidationError(
            "INVALID_ARGUMENT",
            f"{field_name} must be a non-empty string.",
        )
    if len(value) > maximum_chars:
        raise ValidationError(
            "INVALID_ARGUMENT",
            f"{field_name} exceeds the {maximum_chars}-character limit.",
        )
    return value


@dataclass(slots=True)
class TranscriptTurn:
    role: str
    text: str
    category: str
    policy_subcategory: str

    def to_api_dict(self) -> dict[str, str]:
        return {
            "role": self.role,
            "text": self.text,
            "category": self.category,
            "policySubcategory": self.policy_subcategory,
        }

    @classmethod
    def from_remote(
        cls,
        value: Any,
        *,
        category: str,
        policy_subcategory: str,
    ) -> TranscriptTurn | None:
        if not isinstance(value, dict):
            return None
        role = str(value.get("role", "")).strip()
        if role == "system":
            return None
        if role not in {"user", "assistant"}:
            raise ValidationError(
                "SCHEMA_MISMATCH",
                "The remote transcript contains an unexpected role.",
                next_action="Stop the case and inspect the interface contract.",
            )
        text = value.get("text", value.get("content", ""))
        if not isinstance(text, str) or not text.strip():
            raise ValidationError(
                "SCHEMA_MISMATCH",
                "The remote transcript contains an invalid message.",
                next_action="Stop the case and inspect the interface contract.",
            )
        return cls(
            role=role,
            text=text,
            category=category,
            policy_subcategory=policy_subcategory,
        )


@dataclass(slots=True)
class ActionEvent:
    event_type: str
    timestamp: str
    category: str
    policy_subcategory: str
    details: dict[str, Any] = field(default_factory=dict)

    def to_api_dict(self) -> dict[str, Any]:
        return {
            "type": self.event_type,
            "timestamp": self.timestamp,
            "category": self.category,
            "policySubcategory": self.policy_subcategory,
            **self.details,
        }


@dataclass(slots=True)
class ChatResult:
    reply: str
    pending_required: bool
    transcript: list[TranscriptTurn] | None
    stop_reason: str
    http_status: int


@dataclass(slots=True)
class PendingResult:
    pending: bool
    category: str = ""
    policy_subcategory: str = ""
    transcript: list[TranscriptTurn] = field(default_factory=list)
    stop_reason: str = ""


@dataclass(slots=True)
class SubmissionReceipt:
    message: str
    evaluation_id: str
    conversation_id: str


@dataclass(slots=True)
class PreparedSubmission:
    payload: dict[str, Any]
    payload_hash: str
    prepared_at: str


@dataclass(slots=True)
class CaseRecord:
    case_id: str
    experiment_id: str
    category: str
    policy_subcategory: str
    state: CaseState
    created_at: str
    updated_at: str
    transcript: list[TranscriptTurn] = field(default_factory=list)
    action_events: list[ActionEvent] = field(default_factory=list)
    prepared_submission: PreparedSubmission | None = None
    evaluation_id: str = ""
    conversation_id: str = ""
    last_delivery_status: DeliveryStatus = DeliveryStatus.NOT_SENT
    recovery_note: str = ""
    recovery_transcript_hash: str = ""

    def add_event(self, event_type: str, **details: Any) -> None:
        self.action_events.append(
            ActionEvent(
                event_type=event_type,
                timestamp=utc_now(),
                category=self.category,
                policy_subcategory=self.policy_subcategory,
                details=details,
            )
        )
        self.updated_at = utc_now()

    def transcript_hash(self) -> str:
        if self.recovery_transcript_hash and not self.transcript:
            return self.recovery_transcript_hash
        return sha256_json([turn.to_api_dict() for turn in self.transcript])

    def public_dict(self, *, include_transcript: bool = True) -> dict[str, Any]:
        result: dict[str, Any] = {
            "case_id": self.case_id,
            "experiment_id": self.experiment_id,
            "category": self.category,
            "policy_subcategory": self.policy_subcategory,
            "state": self.state.value,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "turn_count": len(self.transcript),
            "transcript_hash": self.transcript_hash(),
            "last_delivery_status": self.last_delivery_status.value,
            "prepared_payload_hash": (
                self.prepared_submission.payload_hash if self.prepared_submission else ""
            ),
            "evaluation_id": self.evaluation_id,
            "conversation_id": self.conversation_id,
            "recovery_note": self.recovery_note,
        }
        if include_transcript:
            result["transcript"] = [turn.to_api_dict() for turn in self.transcript]
        return result

    def journal_metadata(self) -> dict[str, Any]:
        return self.public_dict(include_transcript=False)


@dataclass(frozen=True, slots=True)
class OperationRecord:
    operation_id: str
    tool_name: str
    case_id: str
    request_hash: str
    status: OperationStatus
    created_at: str
    updated_at: str
    result_metadata: dict[str, Any]

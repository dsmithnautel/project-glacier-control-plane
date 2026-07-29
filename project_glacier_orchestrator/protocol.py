"""Closed protocol-v2 schemas for the isolated offline worker."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from .canonical import (
    MAX_PACKET_BYTES,
    MAX_SAFE_INTEGER,
    canonical_json_bytes,
    canonical_json_digest,
    strict_json_loads,
)
from .errors import OrchestratorError

PROTOCOL_VERSION = 2
WORKER_PROMPT_VERSION = "2026-07-12-v1"
EXPECTED_RESULT_SCHEMA = "RESULT_PACKET_V2"
ROLE = "RESEARCH_SCOUT_POOL"
MAX_STRING_LENGTH = 32 * 1024
MAX_ARRAY_LENGTH = 128
MAX_RESULTS = 32
ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,199}$")
DIGEST_PATTERN = re.compile(r"^[0-9a-f]{64}$")
ASSIGNMENT_ID_PATTERN = re.compile(r"^epoch-(?P<epoch>[0-9]+)-assignment-(?P<sequence>[0-9]+)-(?P<suffix>[a-f0-9]{16,64})$")
OFFICIAL_CRITERIA = (
    "Novelty",
    "Realism",
    "Policy relevance",
    "Evidence quality",
    "Likely impact",
)
RECOMMENDATIONS = frozenset({"IMPLEMENT", "RESEARCH", "COMBINE_LATER", "BLOCK", "RETIRE"})
RESULT_STATUSES = frozenset({"COMPLETE", "BLOCKED", "NO_NOVEL_RESULT"})
ACK_DISPOSITIONS = frozenset({"ACCEPTED", "DUPLICATE", "REJECTED"})
REBASE_REASONS = frozenset(
    {"STALE_VERSION", "STATE_HASH_MISMATCH", "RULES_DIGEST_MISMATCH", "LEDGER_DIGEST_MISMATCH", "EPOCH_MISMATCH"}
)
STOP_REASONS = frozenset({"USER_STOP", "ISOLATION_FAILURE", "STATE_DIVERGENCE", "DELIVERY_LOCK", "PILOT_ABORT", "CLEANUP"})
PROTOCOL_REJECT_REASONS = frozenset(
    {"MALFORMED", "UNKNOWN_FIELD", "UNSUPPORTED_VERSION", "UNSUPPORTED_ROLE", "FORBIDDEN_CONTENT", "INSTRUCTION_OVERRIDE", "OTHER"}
)
PROTOCOL_REJECT_DETAILS = "REJECTED_BY_CLOSED_PROTOCOL"
ALLOWED_COLLABORATION_TOOLS = frozenset(
    {"spawn_agent", "send_message", "followup_task", "wait_agent", "interrupt_agent", "list_agents"}
)

_SENSITIVE_PATTERNS = (
    re.compile(r"authorization\s*:\s*(?:bearer|basic)\s+\S+", re.IGNORECASE),
    re.compile(r"cookie\s*:\s*\S+", re.IGNORECASE),
    re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----", re.IGNORECASE),
    re.compile(r"\bocid1\.[a-z0-9.]+", re.IGNORECASE),
    re.compile(r"(?:^|[\s\"'])/(?:Users|home|private|etc|var|tmp)/"),
    re.compile(r"\b[A-Za-z]:\\"),
    re.compile(
        r"\b(?:redirect[_ -]?token|participant[_ -]?(?:id|credential)|GLACIER_BASE_URL|GLACIER_STATE_DIR|"
        r"operation[_ -]?id|prepared[_ -]?payload(?:_hash)?|broker[_ -]?journal)\b",
        re.IGNORECASE,
    ),
)
_ENVELOPE_SMUGGLING_PATTERN = re.compile(r"CONTROL_MESSAGE_JSON:", re.IGNORECASE)
_WORKER_OVERRIDE_PATTERN = re.compile(
    r"\b(?:ignore|replace|override)\b.{0,80}\b(?:worker prompt|offline root instructions)\b",
    re.IGNORECASE | re.DOTALL,
)


@dataclass(frozen=True)
class ResultExpectation:
    transport_thread_id: str
    assignment_id: str
    worker_prompt_version: str
    worker_prompt_digest: str
    leadership_epoch: int
    base_state_version: int
    base_state_hash: str
    rules_digest: str
    ledger_digest: str
    processed_message_ids: frozenset[str]
    processed_result_ids: frozenset[str]


def parse_packet(text: str) -> dict[str, Any]:
    return strict_json_loads(text)


def _exact_keys(value: dict[str, Any], expected: frozenset[str]) -> None:
    if set(value) != expected:
        raise OrchestratorError("CLOSED_SCHEMA_VIOLATION", "The packet fields do not match the closed schema.")


def _wire_bounds(packet: dict[str, Any]) -> None:
    if len(canonical_json_bytes(packet)) > MAX_PACKET_BYTES:
        raise OrchestratorError("PACKET_TOO_LARGE", "The protocol packet exceeds the configured size limit.")


def _string(value: Any, *, allow_empty: bool = False, maximum: int = MAX_STRING_LENGTH) -> str:
    if not isinstance(value, str) or (not allow_empty and not value):
        raise OrchestratorError("STRING_FIELD_INVALID", "A packet string field violates its bounds.")
    try:
        encoded = value.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise OrchestratorError("INVALID_UNICODE", "A packet string field contains invalid Unicode.") from exc
    if len(encoded) > maximum:
        raise OrchestratorError("STRING_FIELD_INVALID", "A packet string field violates its bounds.")
    return value


def _identifier(value: Any) -> str:
    text = _string(value, maximum=200)
    if not ID_PATTERN.fullmatch(text):
        raise OrchestratorError("IDENTIFIER_INVALID", "A packet identifier has an invalid format.")
    return text


def _digest(value: Any) -> str:
    text = _string(value, maximum=64)
    if not DIGEST_PATTERN.fullmatch(text):
        raise OrchestratorError("DIGEST_INVALID", "A packet digest must be lowercase SHA-256 hex.")
    return text


def _nonnegative_int(value: Any) -> int:
    if type(value) is not int or value < 0 or value > MAX_SAFE_INTEGER:
        raise OrchestratorError("INTEGER_FIELD_INVALID", "A packet counter must be a nonnegative integer.")
    return value


def _enum(value: Any, allowed: frozenset[str]) -> str:
    text = _string(value, maximum=64)
    if text not in allowed:
        raise OrchestratorError("ENUM_FIELD_INVALID", "A packet enum value is not allowed.")
    return text


def _string_list(value: Any, *, maximum: int = MAX_ARRAY_LENGTH, require_empty: bool = False) -> list[str]:
    if not isinstance(value, list) or len(value) > maximum:
        raise OrchestratorError("ARRAY_FIELD_INVALID", "A packet array violates its bounds.")
    result = [_string(item) for item in value]
    if require_empty and result:
        raise OrchestratorError("ISOLATION_ATTESTATION_FAILED", "An isolation evidence array must be empty.")
    return result


def _scan_strings(value: Any, *, include_override_scan: bool) -> None:
    pending = [value]
    while pending:
        current = pending.pop()
        if isinstance(current, str):
            if any(pattern.search(current) for pattern in _SENSITIVE_PATTERNS):
                raise OrchestratorError("FORBIDDEN_CONTENT", "The packet contains prohibited sensitive-content markers.")
            if _ENVELOPE_SMUGGLING_PATTERN.search(current):
                raise OrchestratorError("ENVELOPE_SMUGGLING", "The packet attempts to smuggle a control envelope.")
            if include_override_scan and _WORKER_OVERRIDE_PATTERN.search(current):
                raise OrchestratorError("INSTRUCTION_OVERRIDE", "The packet attempts to override the worker contract.")
        elif isinstance(current, dict):
            pending.extend(current.values())
        elif isinstance(current, list):
            pending.extend(current)


def build_attest_request(*, message_id: str, prompt_digest: str) -> dict[str, Any]:
    return {
        "type": "ATTEST_ONLY",
        "protocol_version": PROTOCOL_VERSION,
        "message_id": _identifier(message_id),
        "worker_prompt_version": WORKER_PROMPT_VERSION,
        "worker_prompt_digest": _digest(prompt_digest),
    }


def validate_attest_request(packet: dict[str, Any]) -> None:
    _wire_bounds(packet)
    _exact_keys(
        packet,
        frozenset({"type", "protocol_version", "message_id", "worker_prompt_version", "worker_prompt_digest"}),
    )
    if packet["type"] != "ATTEST_ONLY" or packet["protocol_version"] != PROTOCOL_VERSION:
        raise OrchestratorError("PROTOCOL_VERSION_MISMATCH", "The attestation request uses an unsupported protocol.")
    _identifier(packet["message_id"])
    if packet["worker_prompt_version"] != WORKER_PROMPT_VERSION:
        raise OrchestratorError("PROMPT_VERSION_MISMATCH", "The worker prompt version does not match protocol v2.")
    _digest(packet["worker_prompt_digest"])


def validate_attestation_shape(packet: dict[str, Any]) -> None:
    _wire_bounds(packet)
    _exact_keys(
        packet,
        frozenset(
            {
                "type",
                "protocol_version",
                "message_id",
                "bootstrap_message_id",
                "worker_prompt_version",
                "worker_prompt_digest",
                "target_kind",
                "project_glacier_callable",
                "glacier_callable_tool_names",
                "project_glacier_workspace_context_visible",
                "tool_calls_attempted",
                "files_accessed",
                "files_written",
                "peer_messages_sent",
                "status",
                "failure_reason",
            }
        ),
    )
    if packet["type"] != "OFFLINE_ROOT_ATTESTATION" or packet["protocol_version"] != PROTOCOL_VERSION:
        raise OrchestratorError("PROTOCOL_VERSION_MISMATCH", "The worker attestation uses an unsupported protocol.")
    _identifier(packet["message_id"])
    _identifier(packet["bootstrap_message_id"])
    if packet["worker_prompt_version"] != WORKER_PROMPT_VERSION:
        raise OrchestratorError("PROMPT_VERSION_MISMATCH", "The worker attested to a different prompt version.")
    _digest(packet["worker_prompt_digest"])
    safe = (
        packet["target_kind"] == "projectless"
        and packet["project_glacier_callable"] is False
        and packet["project_glacier_workspace_context_visible"] is False
        and packet["status"] == "PASS"
        and packet["failure_reason"] is None
    )
    if not safe:
        raise OrchestratorError("ISOLATION_ATTESTATION_FAILED", "The worker did not attest to the required isolation boundary.")
    for key in (
        "glacier_callable_tool_names",
        "tool_calls_attempted",
        "files_accessed",
        "files_written",
        "peer_messages_sent",
    ):
        _string_list(packet[key], require_empty=True)


def validate_attestation(
    packet: dict[str, Any], *, expected_message_id: str, expected_prompt_digest: str
) -> None:
    validate_attestation_shape(packet)
    if packet["bootstrap_message_id"] != expected_message_id:
        raise OrchestratorError(
            "ATTESTATION_CORRELATION_MISMATCH",
            "The worker attestation does not match the pending bootstrap.",
        )
    if packet["worker_prompt_digest"] != expected_prompt_digest:
        raise OrchestratorError("PROMPT_DIGEST_MISMATCH", "The worker attested to a different prompt digest.")


ASSIGNMENT_SPEC_KEYS = frozenset(
    {"objective", "allowed_public_context", "coverage_cells", "constraints", "exclusion_list", "stop_conditions"}
)


def validate_assignment_spec(spec: dict[str, Any]) -> None:
    _wire_bounds(spec)
    _exact_keys(spec, ASSIGNMENT_SPEC_KEYS)
    _string(spec["objective"])
    for key in ASSIGNMENT_SPEC_KEYS - {"objective"}:
        _string_list(spec[key])
    _scan_strings(spec, include_override_scan=True)


def build_assignment(
    *,
    spec: dict[str, Any],
    message_id: str,
    assignment_id: str,
    prompt_digest: str,
    leadership_epoch: int,
    base_state_version: int,
    base_state_hash: str,
    rules_digest: str,
    ledger_digest: str,
) -> dict[str, Any]:
    validate_assignment_spec(spec)
    packet = {
        "type": "ASSIGNMENT_PACKET",
        "protocol_version": PROTOCOL_VERSION,
        "message_id": _identifier(message_id),
        "assignment_id": _identifier(assignment_id),
        "role": ROLE,
        "worker_prompt_version": WORKER_PROMPT_VERSION,
        "worker_prompt_digest": _digest(prompt_digest),
        "leadership_epoch": _nonnegative_int(leadership_epoch),
        "base_state_version": _nonnegative_int(base_state_version),
        "base_state_hash": _digest(base_state_hash),
        "rules_digest": _digest(rules_digest),
        "ledger_digest": _digest(ledger_digest),
        "objective": spec["objective"],
        "allowed_public_context": spec["allowed_public_context"],
        "coverage_cells": spec["coverage_cells"],
        "constraints": spec["constraints"],
        "exclusion_list": spec["exclusion_list"],
        "expected_result_schema": EXPECTED_RESULT_SCHEMA,
        "stop_conditions": spec["stop_conditions"],
    }
    validate_assignment_packet(packet)
    return packet


def validate_assignment_packet(packet: dict[str, Any]) -> None:
    _wire_bounds(packet)
    expected = frozenset(
        {
            "type",
            "protocol_version",
            "message_id",
            "assignment_id",
            "role",
            "worker_prompt_version",
            "worker_prompt_digest",
            "leadership_epoch",
            "base_state_version",
            "base_state_hash",
            "rules_digest",
            "ledger_digest",
            "objective",
            "allowed_public_context",
            "coverage_cells",
            "constraints",
            "exclusion_list",
            "expected_result_schema",
            "stop_conditions",
        }
    )
    _exact_keys(packet, expected)
    if packet["type"] != "ASSIGNMENT_PACKET" or packet["protocol_version"] != PROTOCOL_VERSION:
        raise OrchestratorError("PROTOCOL_VERSION_MISMATCH", "The assignment uses an unsupported protocol.")
    _identifier(packet["message_id"])
    assignment_id = _identifier(packet["assignment_id"])
    match = ASSIGNMENT_ID_PATTERN.fullmatch(assignment_id)
    if match is None:
        raise OrchestratorError("ASSIGNMENT_ID_INVALID", "The assignment ID does not encode its epoch and sequence.")
    if packet["role"] != ROLE or packet["expected_result_schema"] != EXPECTED_RESULT_SCHEMA:
        raise OrchestratorError("UNSUPPORTED_ROLE", "The assignment role or result schema is unsupported.")
    if packet["worker_prompt_version"] != WORKER_PROMPT_VERSION:
        raise OrchestratorError("PROMPT_VERSION_MISMATCH", "The assignment uses a different worker prompt version.")
    _digest(packet["worker_prompt_digest"])
    epoch = _nonnegative_int(packet["leadership_epoch"])
    _nonnegative_int(packet["base_state_version"])
    encoded_epoch = int(match.group("epoch"))
    encoded_sequence = int(match.group("sequence"))
    if encoded_epoch > MAX_SAFE_INTEGER or encoded_sequence > MAX_SAFE_INTEGER:
        raise OrchestratorError("ASSIGNMENT_ID_INVALID", "The assignment ID counters exceed the safe range.")
    if encoded_epoch != epoch:
        raise OrchestratorError("ASSIGNMENT_EPOCH_MISMATCH", "The assignment ID and leadership epoch disagree.")
    if encoded_sequence < 1:
        raise OrchestratorError("ASSIGNMENT_ID_INVALID", "The assignment sequence must start at one.")
    for key in ("base_state_hash", "rules_digest", "ledger_digest"):
        _digest(packet[key])
    _string(packet["objective"])
    for key in ("allowed_public_context", "coverage_cells", "constraints", "exclusion_list", "stop_conditions"):
        _string_list(packet[key])
    _scan_strings(
        {
            key: packet[key]
            for key in ("objective", "allowed_public_context", "coverage_cells", "constraints", "exclusion_list", "stop_conditions")
        },
        include_override_scan=True,
    )


def _validate_research_item(item: Any) -> None:
    if not isinstance(item, dict):
        raise OrchestratorError("RESULT_ITEM_INVALID", "A research result item must be an object.")
    expected = frozenset(
        {
            "family_id",
            "hypothesis_id",
            "policy_category_subcategory",
            "causal_mechanism",
            "material_novelty_delta",
            "synthetic_oci_scenario",
            "smallest_controlled_experiment",
            "expected_observations",
            "benign_or_negative_control",
            "scope_and_safety_analysis",
            "dependencies_or_required_discovery",
            "estimated_value_across_five_official_criteria",
            "falsifier",
            "recommended_next_action",
        }
    )
    _exact_keys(item, expected)
    for key in (
        "family_id",
        "hypothesis_id",
        "policy_category_subcategory",
        "causal_mechanism",
        "material_novelty_delta",
        "synthetic_oci_scenario",
        "smallest_controlled_experiment",
        "scope_and_safety_analysis",
        "falsifier",
    ):
        _string(item[key])
    control = item["benign_or_negative_control"]
    if control is not None:
        _string(control)
    _string_list(item["dependencies_or_required_discovery"])
    expected_observations = item["expected_observations"]
    if not isinstance(expected_observations, dict):
        raise OrchestratorError("RESULT_ITEM_INVALID", "Expected observations must be an object.")
    _exact_keys(expected_observations, frozenset({"allow", "block"}))
    _string(expected_observations["allow"])
    _string(expected_observations["block"])
    criteria = item["estimated_value_across_five_official_criteria"]
    if not isinstance(criteria, list) or len(criteria) != len(OFFICIAL_CRITERIA):
        raise OrchestratorError("CRITERIA_INVALID", "A result must assess exactly the five official criteria.")
    observed_names: list[str] = []
    for criterion in criteria:
        if not isinstance(criterion, dict):
            raise OrchestratorError("CRITERIA_INVALID", "Each criterion assessment must be an object.")
        _exact_keys(criterion, frozenset({"criterion", "assessment"}))
        observed_names.append(_string(criterion["criterion"], maximum=64))
        _string(criterion["assessment"])
    if tuple(observed_names) != OFFICIAL_CRITERIA:
        raise OrchestratorError("CRITERIA_INVALID", "Criterion names and order must match the official rubric.")
    _enum(item["recommended_next_action"], RECOMMENDATIONS)


def validate_result_packet(packet: dict[str, Any]) -> str:
    _wire_bounds(packet)
    expected = frozenset(
        {
            "type",
            "protocol_version",
            "message_id",
            "assignment_id",
            "worker_task_id",
            "role",
            "worker_prompt_version",
            "worker_prompt_digest",
            "leadership_epoch",
            "base_state_version",
            "base_state_hash",
            "rules_digest",
            "ledger_digest",
            "status",
            "result_digest",
            "results",
            "assumptions",
            "evidence_basis",
            "duplicate_risk",
            "recommended_next_action",
            "child_assignments",
            "children_running",
            "tool_calls_attempted",
            "files_accessed",
            "files_written",
            "peer_messages_sent",
            "claims_of_live_action",
        }
    )
    _exact_keys(packet, expected)
    if packet["type"] != "RESULT_PACKET" or packet["protocol_version"] != PROTOCOL_VERSION:
        raise OrchestratorError("PROTOCOL_VERSION_MISMATCH", "The result uses an unsupported protocol.")
    _identifier(packet["message_id"])
    _identifier(packet["assignment_id"])
    worker_task_id = packet["worker_task_id"]
    if worker_task_id is not None:
        _identifier(worker_task_id)
    if packet["role"] != ROLE or packet["worker_prompt_version"] != WORKER_PROMPT_VERSION:
        raise OrchestratorError("UNSUPPORTED_ROLE", "The result role or worker prompt version is unsupported.")
    _digest(packet["worker_prompt_digest"])
    _nonnegative_int(packet["leadership_epoch"])
    _nonnegative_int(packet["base_state_version"])
    for key in ("base_state_hash", "rules_digest", "ledger_digest"):
        _digest(packet[key])
    status = _enum(packet["status"], RESULT_STATUSES)
    supplied_digest = packet["result_digest"]
    if supplied_digest is not None:
        raise OrchestratorError("RESULT_DIGEST_MUST_BE_NULL", "The worker must leave result_digest null for R0 canonicalization.")
    results = packet["results"]
    if not isinstance(results, list) or len(results) > MAX_RESULTS:
        raise OrchestratorError("RESULTS_INVALID", "The result item array violates its bounds.")
    for item in results:
        _validate_research_item(item)
    if status == "COMPLETE" and not results:
        raise OrchestratorError("RESULTS_INVALID", "A complete result must contain at least one research item.")
    if status != "COMPLETE" and results:
        raise OrchestratorError("RESULTS_INVALID", "A blocked or no-novel-result packet must not contain research items.")
    _string_list(packet["assumptions"])
    evidence = packet["evidence_basis"]
    if not isinstance(evidence, list) or len(evidence) > MAX_ARRAY_LENGTH:
        raise OrchestratorError("EVIDENCE_BASIS_INVALID", "The evidence basis array violates its bounds.")
    for item in evidence:
        if not isinstance(item, dict):
            raise OrchestratorError("EVIDENCE_BASIS_INVALID", "Each evidence basis item must be an object.")
        _exact_keys(item, frozenset({"claim", "basis"}))
        _string(item["claim"])
        _enum(item["basis"], frozenset({"PACKET_FACT", "INFERENCE"}))
    _string(packet["duplicate_risk"])
    _enum(packet["recommended_next_action"], RECOMMENDATIONS)
    _string_list(packet["child_assignments"])
    if _nonnegative_int(packet["children_running"]) != 0:
        raise OrchestratorError("WORKER_CHILDREN_RUNNING", "A result cannot be accepted while worker children are running.")
    tool_calls = _string_list(packet["tool_calls_attempted"])
    if any(tool not in ALLOWED_COLLABORATION_TOOLS for tool in tool_calls):
        raise OrchestratorError("FORBIDDEN_TOOL_CALL", "The worker reported a non-collaboration tool call.")
    for key in ("files_accessed", "files_written", "peer_messages_sent"):
        _string_list(packet[key], require_empty=True)
    if packet["claims_of_live_action"] != "NONE":
        raise OrchestratorError("LIVE_ACTION_CLAIMED", "The worker claimed a prohibited live action.")
    _scan_strings(
        {
            "results": results,
            "assumptions": packet["assumptions"],
            "evidence_basis": evidence,
            "duplicate_risk": packet["duplicate_risk"],
            "child_assignments": packet["child_assignments"],
        },
        include_override_scan=False,
    )
    canonical = dict(packet)
    canonical["result_digest"] = None
    computed = canonical_json_digest(canonical)
    if supplied_digest is not None and supplied_digest != computed:
        raise OrchestratorError("RESULT_DIGEST_MISMATCH", "The worker-supplied result digest is incorrect.")
    return computed


def validate_result_against_expectation(packet: dict[str, Any], expectation: ResultExpectation) -> str:
    result_digest = validate_result_packet(packet)
    message_id = packet["message_id"]
    if message_id in expectation.processed_message_ids or result_digest in expectation.processed_result_ids:
        raise OrchestratorError("RESULT_REPLAY", "The result message has already been processed.")
    if packet["assignment_id"] != expectation.assignment_id:
        raise OrchestratorError("ASSIGNMENT_MISMATCH", "The result does not match the active assignment.")
    if packet["worker_task_id"] is not None and packet["worker_task_id"] != expectation.transport_thread_id:
        raise OrchestratorError("WORKER_IDENTITY_MISMATCH", "The reported worker identity differs from the transport identity.")
    checks = (
        (packet["worker_prompt_version"], expectation.worker_prompt_version, "PROMPT_VERSION_MISMATCH"),
        (packet["worker_prompt_digest"], expectation.worker_prompt_digest, "PROMPT_DIGEST_MISMATCH"),
        (packet["leadership_epoch"], expectation.leadership_epoch, "EPOCH_MISMATCH"),
        (packet["base_state_version"], expectation.base_state_version, "STALE_VERSION"),
        (packet["base_state_hash"], expectation.base_state_hash, "STATE_HASH_MISMATCH"),
        (packet["rules_digest"], expectation.rules_digest, "RULES_DIGEST_MISMATCH"),
        (packet["ledger_digest"], expectation.ledger_digest, "LEDGER_DIGEST_MISMATCH"),
    )
    for actual, expected, code in checks:
        if actual != expected:
            raise OrchestratorError(code, "The result snapshot does not match the active assignment.")
    return result_digest


def build_ack(
    *,
    message_id: str,
    reply_to_message_id: str,
    assignment_id: str,
    leadership_epoch: int,
    accepted_state_version: int,
    accepted_state_hash: str,
    canonical_result_digest: str,
    disposition: str,
) -> dict[str, Any]:
    packet = {
        "type": "ACK",
        "protocol_version": PROTOCOL_VERSION,
        "message_id": _identifier(message_id),
        "reply_to_message_id": _identifier(reply_to_message_id),
        "assignment_id": _identifier(assignment_id),
        "leadership_epoch": _nonnegative_int(leadership_epoch),
        "accepted_state_version": _nonnegative_int(accepted_state_version),
        "accepted_state_hash": _digest(accepted_state_hash),
        "canonical_result_digest": _digest(canonical_result_digest),
        "disposition": _enum(disposition, ACK_DISPOSITIONS),
    }
    validate_ack(packet)
    return packet


def validate_ack(packet: dict[str, Any]) -> None:
    _wire_bounds(packet)
    _exact_keys(
        packet,
        frozenset(
            {
                "type",
                "protocol_version",
                "message_id",
                "reply_to_message_id",
                "assignment_id",
                "leadership_epoch",
                "accepted_state_version",
                "accepted_state_hash",
                "canonical_result_digest",
                "disposition",
            }
        ),
    )
    if packet["type"] != "ACK" or packet["protocol_version"] != PROTOCOL_VERSION:
        raise OrchestratorError("PROTOCOL_VERSION_MISMATCH", "The ACK uses an unsupported protocol.")
    for key in ("message_id", "reply_to_message_id", "assignment_id"):
        _identifier(packet[key])
    _nonnegative_int(packet["leadership_epoch"])
    _nonnegative_int(packet["accepted_state_version"])
    _digest(packet["accepted_state_hash"])
    _digest(packet["canonical_result_digest"])
    _enum(packet["disposition"], ACK_DISPOSITIONS)


def build_rebase(
    *, message_id: str, reply_to_message_id: str, assignment_id: str, leadership_epoch: int, reason_code: str
) -> dict[str, Any]:
    packet = {
        "type": "REBASE_REQUIRED",
        "protocol_version": PROTOCOL_VERSION,
        "message_id": _identifier(message_id),
        "reply_to_message_id": _identifier(reply_to_message_id),
        "assignment_id": _identifier(assignment_id),
        "leadership_epoch": _nonnegative_int(leadership_epoch),
        "reason_code": _enum(reason_code, REBASE_REASONS),
    }
    validate_rebase(packet)
    return packet


def validate_rebase(packet: dict[str, Any]) -> None:
    _wire_bounds(packet)
    _exact_keys(packet, frozenset({"type", "protocol_version", "message_id", "reply_to_message_id", "assignment_id", "leadership_epoch", "reason_code"}))
    if packet["type"] != "REBASE_REQUIRED" or packet["protocol_version"] != PROTOCOL_VERSION:
        raise OrchestratorError("PROTOCOL_VERSION_MISMATCH", "The rebase message uses an unsupported protocol.")
    for key in ("message_id", "reply_to_message_id", "assignment_id"):
        _identifier(packet[key])
    _nonnegative_int(packet["leadership_epoch"])
    _enum(packet["reason_code"], REBASE_REASONS)


def build_stop(*, message_id: str, leadership_epoch: int, assignment_id: str | None, reason_code: str) -> dict[str, Any]:
    packet = {
        "type": "STOP_ALL",
        "protocol_version": PROTOCOL_VERSION,
        "message_id": _identifier(message_id),
        "leadership_epoch": _nonnegative_int(leadership_epoch),
        "assignment_id": None if assignment_id is None else _identifier(assignment_id),
        "reason_code": _enum(reason_code, STOP_REASONS),
    }
    validate_stop(packet)
    return packet


def validate_stop(packet: dict[str, Any]) -> None:
    _wire_bounds(packet)
    _exact_keys(packet, frozenset({"type", "protocol_version", "message_id", "leadership_epoch", "assignment_id", "reason_code"}))
    if packet["type"] != "STOP_ALL" or packet["protocol_version"] != PROTOCOL_VERSION:
        raise OrchestratorError("PROTOCOL_VERSION_MISMATCH", "The stop message uses an unsupported protocol.")
    _identifier(packet["message_id"])
    _nonnegative_int(packet["leadership_epoch"])
    if packet["assignment_id"] is not None:
        _identifier(packet["assignment_id"])
    _enum(packet["reason_code"], STOP_REASONS)


def validate_stop_ack_shape(packet: dict[str, Any]) -> None:
    _wire_bounds(packet)
    _exact_keys(
        packet,
        frozenset(
            {
                "type",
                "protocol_version",
                "message_id",
                "reply_to_message_id",
                "leadership_epoch",
                "assignment_id",
                "children_running",
                "files_written",
                "live_actions",
            }
        ),
    )
    if packet["type"] != "STOP_ACK" or packet["protocol_version"] != PROTOCOL_VERSION:
        raise OrchestratorError("PROTOCOL_VERSION_MISMATCH", "The stop acknowledgement uses an unsupported protocol.")
    _identifier(packet["message_id"])
    _identifier(packet["reply_to_message_id"])
    _nonnegative_int(packet["leadership_epoch"])
    assignment_id = packet["assignment_id"]
    if assignment_id is not None:
        _identifier(assignment_id)
    children_running = _nonnegative_int(packet["children_running"])
    if children_running != 0:
        raise OrchestratorError("WORKER_CHILDREN_RUNNING", "The worker still reports active children.")
    _string_list(packet["files_written"], require_empty=True)
    if packet["live_actions"] != "NONE":
        raise OrchestratorError("LIVE_ACTION_CLAIMED", "The worker reported a prohibited live action.")


def validate_stop_ack(
    packet: dict[str, Any], *, expected_stop_message_id: str, expected_epoch: int, expected_assignment_id: str | None
) -> None:
    validate_stop_ack_shape(packet)
    if packet["reply_to_message_id"] != expected_stop_message_id:
        raise OrchestratorError("STOP_ACK_MISMATCH", "The stop acknowledgement does not match the pending stop.")
    if packet["leadership_epoch"] != expected_epoch:
        raise OrchestratorError("EPOCH_MISMATCH", "The stop acknowledgement uses a different leadership epoch.")
    if packet["assignment_id"] != expected_assignment_id:
        raise OrchestratorError("STOP_ACK_MISMATCH", "The stop acknowledgement references a different assignment.")


def validate_protocol_reject(packet: dict[str, Any]) -> None:
    _wire_bounds(packet)
    _exact_keys(
        packet,
        frozenset(
            {
                "type",
                "protocol_version",
                "message_id",
                "reply_to_message_id",
                "assignment_id",
                "leadership_epoch",
                "reason_code",
                "details",
                "prohibited_data_echoed",
                "tool_calls_attempted",
                "files_accessed",
                "files_written",
                "peer_messages_sent",
            }
        ),
    )
    if packet["type"] != "PROTOCOL_REJECT" or packet["protocol_version"] != PROTOCOL_VERSION:
        raise OrchestratorError("PROTOCOL_VERSION_MISMATCH", "The protocol rejection uses an unsupported protocol.")
    _identifier(packet["message_id"])
    for key in ("reply_to_message_id", "assignment_id"):
        if packet[key] is not None:
            _identifier(packet[key])
    if packet["leadership_epoch"] is not None:
        _nonnegative_int(packet["leadership_epoch"])
    _enum(packet["reason_code"], PROTOCOL_REJECT_REASONS)
    if packet["details"] != PROTOCOL_REJECT_DETAILS:
        raise OrchestratorError("PROTOCOL_REJECT_DETAILS_INVALID", "The rejection details must use the fixed safe code.")
    if packet["prohibited_data_echoed"] is not False:
        raise OrchestratorError("PROHIBITED_DATA_ECHOED", "The rejection packet reports prohibited-data echoing.")
    tool_calls = _string_list(packet["tool_calls_attempted"])
    if any(tool not in ALLOWED_COLLABORATION_TOOLS for tool in tool_calls):
        raise OrchestratorError("FORBIDDEN_TOOL_CALL", "The worker reported a non-collaboration tool call.")
    for key in ("files_accessed", "files_written", "peer_messages_sent"):
        _string_list(packet[key], require_empty=True)


def validate_closed_packet(packet: dict[str, Any]) -> None:
    """Validate any protocol message when no external correlation context is needed."""

    packet_type = packet.get("type")
    validators = {
        "ATTEST_ONLY": validate_attest_request,
        "OFFLINE_ROOT_ATTESTATION": validate_attestation_shape,
        "ASSIGNMENT_PACKET": validate_assignment_packet,
        "RESULT_PACKET": lambda value: validate_result_packet(value),
        "PROTOCOL_REJECT": validate_protocol_reject,
        "ACK": validate_ack,
        "REBASE_REQUIRED": validate_rebase,
        "STOP_ALL": validate_stop,
        "STOP_ACK": validate_stop_ack_shape,
    }
    validator = validators.get(packet_type)
    if validator is None:
        raise OrchestratorError("MESSAGE_TYPE_UNSUPPORTED", "The protocol message type is not valid in this context.")
    validator(packet)

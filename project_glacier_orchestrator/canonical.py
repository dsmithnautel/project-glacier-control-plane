"""Strict JSON parsing and deterministic hashing for the closed protocol subset."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from typing import Any

from .errors import OrchestratorError

MAX_PACKET_BYTES = 512 * 1024
MAX_JSON_DEPTH = 20
MAX_SAFE_INTEGER = (2**53) - 1


def _reject_constant(value: str) -> None:
    raise OrchestratorError("INVALID_JSON_NUMBER", "Non-finite JSON numbers are not allowed.")


def _reject_float(value: str) -> None:
    raise OrchestratorError("FLOAT_NOT_ALLOWED", "Floating-point values are not part of this protocol.")


def _parse_int(value: str) -> int:
    digits = value[1:] if value.startswith("-") else value
    if len(digits) > 16:
        raise OrchestratorError("INTEGER_OUT_OF_RANGE", "The JSON integer exceeds the safe protocol range.")
    try:
        parsed = int(value, 10)
    except ValueError as exc:
        raise OrchestratorError("INVALID_JSON_NUMBER", "The JSON integer is invalid.") from exc
    if abs(parsed) > MAX_SAFE_INTEGER:
        raise OrchestratorError("INTEGER_OUT_OF_RANGE", "The JSON integer exceeds the safe protocol range.")
    return parsed


def _object_without_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise OrchestratorError("DUPLICATE_JSON_KEY", "The JSON object contains a duplicate key.")
        result[key] = value
    return result


def strict_json_loads(text: str, *, max_bytes: int = MAX_PACKET_BYTES) -> dict[str, Any]:
    """Parse exactly one closed-protocol JSON object and reject ambiguous encodings."""

    try:
        encoded = text.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise OrchestratorError("INVALID_UNICODE", "The JSON document contains invalid Unicode.") from exc
    if len(encoded) > max_bytes:
        raise OrchestratorError("PACKET_TOO_LARGE", "The JSON document exceeds the configured size limit.")
    if text.startswith("\ufeff"):
        raise OrchestratorError("JSON_BOM_NOT_ALLOWED", "A JSON byte-order mark is not allowed.")
    try:
        value = json.loads(
            text,
            object_pairs_hook=_object_without_duplicates,
            parse_constant=_reject_constant,
            parse_float=_reject_float,
            parse_int=_parse_int,
        )
    except OrchestratorError:
        raise
    except (json.JSONDecodeError, RecursionError, ValueError) as exc:
        raise OrchestratorError("MALFORMED_JSON", "The packet is not exactly one valid JSON object.") from exc
    if not isinstance(value, dict):
        raise OrchestratorError("JSON_OBJECT_REQUIRED", "The packet root must be a JSON object.")
    _validate_canonical_subset(value)
    return value


def _validate_canonical_subset(value: Any, *, depth: int = 0) -> None:
    if depth > MAX_JSON_DEPTH:
        raise OrchestratorError("JSON_TOO_DEEP", "The JSON document exceeds the nesting limit.")
    if value is None or type(value) is bool or isinstance(value, str):
        if isinstance(value, str):
            try:
                value.encode("utf-8")
            except UnicodeEncodeError as exc:
                raise OrchestratorError("INVALID_UNICODE", "The JSON document contains invalid Unicode.") from exc
        return
    if type(value) is int:
        if abs(value) > MAX_SAFE_INTEGER:
            raise OrchestratorError("INTEGER_OUT_OF_RANGE", "The JSON integer exceeds the safe protocol range.")
        return
    if isinstance(value, float):
        raise OrchestratorError("FLOAT_NOT_ALLOWED", "Floating-point values are not part of this protocol.")
    if isinstance(value, Mapping):
        for key, nested in value.items():
            if not isinstance(key, str) or not key.isascii():
                raise OrchestratorError("NON_ASCII_SCHEMA_KEY", "Protocol object keys must be ASCII.")
            _validate_canonical_subset(nested, depth=depth + 1)
        return
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        for nested in value:
            _validate_canonical_subset(nested, depth=depth + 1)
        return
    raise OrchestratorError("UNSUPPORTED_JSON_TYPE", "The JSON document contains an unsupported value type.")


def canonical_json_bytes(value: Any) -> bytes:
    """Serialize the validated protocol subset deterministically as UTF-8 JSON.

    Protocol schemas use fixed ASCII keys, reject floats and out-of-range integers, and reject
    invalid Unicode before this function runs. Within that deliberately restricted subset,
    sorted compact JSON is stable across supported Python runtimes.
    """

    _validate_canonical_subset(value)
    try:
        text = json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        )
        return text.encode("utf-8")
    except (TypeError, ValueError, UnicodeEncodeError) as exc:
        raise OrchestratorError("CANONICALIZATION_FAILED", "The value cannot be canonicalized safely.") from exc


def sha256_hex(data: bytes) -> str:
    """Return a lowercase SHA-256 digest."""

    return hashlib.sha256(data).hexdigest()


def canonical_json_digest(value: Any) -> str:
    """Hash a value using the protocol's restricted canonical JSON encoding."""

    return sha256_hex(canonical_json_bytes(value))

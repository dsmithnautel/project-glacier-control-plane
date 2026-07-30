"""Conservative secret redaction for errors and local metadata."""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from typing import Any

_JWT = re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\b")
_TOKEN_QUERY = re.compile(
    r"([?&](?:access_token|auth|jwt|session|token)=)[^&#\s]+", re.IGNORECASE
)
_AUTH_VALUE = re.compile(r"\b(Bearer|Basic)\s+[A-Za-z0-9._~+/=-]+", re.IGNORECASE)
_COOKIE_VALUE = re.compile(
    r"\b(Cookie|Set-Cookie)\s*:\s*[^\r\n]+", re.IGNORECASE
)


def redact_text(value: str, *, additional_secrets: Sequence[str] = ()) -> str:
    """Remove known credential patterns without rewriting ordinary challenge text."""

    redacted = _JWT.sub("[REDACTED_JWT]", value)
    redacted = _TOKEN_QUERY.sub(r"\1[REDACTED]", redacted)
    redacted = _AUTH_VALUE.sub(r"\1 [REDACTED]", redacted)
    redacted = _COOKIE_VALUE.sub(r"\1: [REDACTED]", redacted)
    for secret in additional_secrets:
        if secret:
            redacted = redacted.replace(secret, "[REDACTED_CREDENTIAL]")
    return redacted


def sanitize_value(value: Any, *, additional_secrets: Sequence[str] = ()) -> Any:
    """Recursively sanitize a JSON-compatible value."""

    if isinstance(value, str):
        return redact_text(value, additional_secrets=additional_secrets)
    if isinstance(value, Mapping):
        return {
            str(key): sanitize_value(item, additional_secrets=additional_secrets)
            for key, item in value.items()
        }
    if isinstance(value, Sequence) and not isinstance(value, (bytes, bytearray, str)):
        return [sanitize_value(item, additional_secrets=additional_secrets) for item in value]
    return value

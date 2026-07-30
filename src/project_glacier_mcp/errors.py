"""Sanitized domain errors returned by the broker."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(slots=True)
class GlacierError(Exception):
    """An expected failure safe to expose through an MCP tool result."""

    code: str
    message: str
    retryable: bool = False
    delivery_status: str = "NOT_SENT"
    next_action: str = "Inspect the error and correct the request."
    http_status: int | None = None

    def __str__(self) -> str:
        return self.message

    def as_dict(self, *, case_state: str | None = None) -> dict[str, Any]:
        result: dict[str, Any] = {
            "code": self.code,
            "message": self.message,
            "retryable": self.retryable,
            "delivery_status": self.delivery_status,
            "next_action": self.next_action,
        }
        if case_state is not None:
            result["case_state"] = case_state
        return result


class ConfigurationError(GlacierError):
    """The local broker configuration is missing or unsafe."""


class ValidationError(GlacierError):
    """A caller supplied an invalid argument."""


class InvalidStateError(GlacierError):
    """The requested operation is invalid for the current state."""


class RemoteRejectedError(GlacierError):
    """The remote service definitively rejected a request."""


class DeliveryUnknownError(GlacierError):
    """A state-changing request may have reached the remote service."""


class AuthenticationError(GlacierError):
    """Authentication could not be established safely."""

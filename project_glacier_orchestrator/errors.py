"""Public, secret-safe error types for the orchestrator CLI."""

from __future__ import annotations


class OrchestratorError(Exception):
    """An expected fail-closed error with a stable machine-readable code."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message

    def as_dict(self) -> dict[str, str]:
        """Return a JSON-safe error without raw packet or secret content."""

        return {"code": self.code, "message": self.message}

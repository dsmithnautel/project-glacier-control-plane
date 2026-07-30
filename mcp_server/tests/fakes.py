from __future__ import annotations

from collections import deque
from typing import Any

from project_glacier_mcp.models import (
    ChatResult,
    PendingResult,
    SubmissionReceipt,
)


class FakeTransport:
    def __init__(self) -> None:
        self._authenticated = False
        self.pending = PendingResult(pending=False)
        self.chat_results: deque[ChatResult | Exception] = deque()
        self.clear_result: dict[str, Any] | Exception = {"message": "Chat cleared."}
        self.evaluate_result: SubmissionReceipt | Exception = SubmissionReceipt(
            message="Evaluation saved.",
            evaluation_id="eval-1",
            conversation_id="conv-1",
        )
        self.chat_calls: list[dict[str, str]] = []
        self.clear_calls = 0
        self.evaluate_calls: list[dict[str, Any]] = []

    @property
    def authenticated(self) -> bool:
        return self._authenticated

    def authenticate(self) -> None:
        self._authenticated = True

    def get_openapi(self) -> dict[str, Any]:
        return {
            "openapi": "3.1.0",
            "paths": {
                "/api/chat": {},
                "/api/clear_chat": {},
                "/api/evaluation": {},
                "/api/pending-evaluation": {},
            },
        }

    def get_pending(self) -> PendingResult:
        return self.pending

    def chat(
        self,
        *,
        category: str,
        policy_subcategory: str,
        message: str,
    ) -> ChatResult:
        self.chat_calls.append(
            {
                "category": category,
                "policy_subcategory": policy_subcategory,
                "message": message,
            }
        )
        result = self.chat_results.popleft()
        if isinstance(result, Exception):
            raise result
        return result

    def clear(self) -> dict[str, Any]:
        self.clear_calls += 1
        if isinstance(self.clear_result, Exception):
            raise self.clear_result
        return self.clear_result

    def evaluate(self, payload: dict[str, Any]) -> SubmissionReceipt:
        self.evaluate_calls.append(payload)
        if isinstance(self.evaluate_result, Exception):
            raise self.evaluate_result
        return self.evaluate_result

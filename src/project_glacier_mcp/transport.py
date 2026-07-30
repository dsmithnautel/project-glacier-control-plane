"""Strict transport for the authorized Project Glacier challenge API."""

from __future__ import annotations

import json
from collections.abc import Iterator
from typing import Any, Protocol
from urllib.parse import urljoin, urlsplit

import requests
from requests import Response, Session

from .errors import (
    AuthenticationError,
    DeliveryUnknownError,
    RemoteRejectedError,
    ValidationError,
)
from .models import (
    ChatResult,
    PendingResult,
    SubmissionReceipt,
    TranscriptTurn,
    validate_category,
)
from .redaction import redact_text
from .settings import EXPECTED_ORIGIN, Settings

_REQUIRED_API_PATHS = {
    "/api/chat",
    "/api/clear_chat",
    "/api/evaluation",
    "/api/pending-evaluation",
}
_ALLOWED_PATHS = _REQUIRED_API_PATHS | {"/", "/evaluation", "/openapi.json"}
_AUTH_COOKIE = "policy_verifier_mockup_auth"
_CHAT_COOKIE = "policy_verifier_mockup_chat"


class ChallengeTransport(Protocol):
    @property
    def authenticated(self) -> bool: ...

    def authenticate(self) -> None: ...

    def get_openapi(self) -> dict[str, Any]: ...

    def get_pending(self) -> PendingResult: ...

    def chat(
        self,
        *,
        category: str,
        policy_subcategory: str,
        message: str,
    ) -> ChatResult: ...

    def clear(self) -> dict[str, Any]: ...

    def evaluate(self, payload: dict[str, Any]) -> SubmissionReceipt: ...


class RequestsChallengeTransport:
    """Requests-based transport with no redirects, proxies, or automatic retries."""

    def __init__(
        self,
        settings: Settings,
        *,
        session: Session | None = None,
        expected_origin: str = EXPECTED_ORIGIN,
    ) -> None:
        self._settings = settings
        self._origin = settings.base_url
        self._expected_origin = expected_origin
        self._session = requests.Session() if session is None else session
        self._session.trust_env = False
        self._session.headers.update(
            {
                "Accept": "application/json, text/html;q=0.9",
                "User-Agent": "project-glacier-mcp/0.1",
            }
        )
        self._authenticated = False

    @property
    def authenticated(self) -> bool:
        return self._authenticated

    @property
    def _timeout(self) -> tuple[int, int]:
        return (
            self._settings.connect_timeout_seconds,
            self._settings.read_timeout_seconds,
        )

    def _url_for_path(self, path: str) -> str:
        if path not in _ALLOWED_PATHS:
            raise ValidationError(
                "INVALID_ARGUMENT",
                "The requested challenge path is not allowlisted.",
            )
        return f"{self._origin}{path}"

    def _validate_same_origin_url(self, value: str, *, allowed_paths: set[str]) -> str:
        parsed = urlsplit(value)
        origin = f"{parsed.scheme}://{parsed.hostname}:{parsed.port}"
        if (
            origin != self._expected_origin
            or parsed.username is not None
            or parsed.password is not None
            or parsed.fragment
            or parsed.path not in allowed_paths
        ):
            raise AuthenticationError(
                "AUTH_REDIRECT_REJECTED",
                "The authentication service returned an unsafe redirect.",
                next_action="Stop and inspect the challenge authentication flow.",
            )
        return value

    def authenticate(self) -> None:
        if self._authenticated:
            return

        current_url = self._url_for_path("/")
        params: dict[str, str] | None = {"user_id": self._settings.user_id}

        for _ in range(5):
            try:
                response = self._session.get(
                    current_url,
                    params=params,
                    allow_redirects=False,
                    headers={"Referer": ""},
                    stream=True,
                    timeout=self._timeout,
                )
            except requests.RequestException as exc:
                raise AuthenticationError(
                    "AUTH_FAILED",
                    "The challenge authentication request could not be completed.",
                    retryable=True,
                    next_action="Verify VPN connectivity and try opening the session again.",
                ) from exc

            params = None
            with response:
                if response.status_code in {301, 302, 303, 307, 308}:
                    location = response.headers.get("Location")
                    if not location:
                        raise AuthenticationError(
                            "AUTH_FAILED",
                            "The authentication redirect did not include a destination.",
                        )
                    candidate = urljoin(current_url, location)
                    current_url = self._validate_same_origin_url(
                        candidate,
                        allowed_paths={"/", "/evaluation", "/login"},
                    )
                    continue

                parsed_current = urlsplit(current_url)
                if response.status_code != 200 or parsed_current.path != "/evaluation":
                    raise AuthenticationError(
                        "AUTH_FAILED",
                        "The participant credential was not accepted by the challenge service.",
                        next_action="Verify the participant credential and event access.",
                    )

                cookie_names = {cookie.name for cookie in self._session.cookies}
                if not {_AUTH_COOKIE, _CHAT_COOKIE}.issubset(cookie_names):
                    raise AuthenticationError(
                        "AUTH_FAILED",
                        "The challenge service did not establish the required session cookies.",
                    )
                for cookie in self._session.cookies:
                    domain = cookie.domain.lstrip(".")
                    if domain and domain != "144.25.123.139":
                        raise AuthenticationError(
                            "AUTH_FAILED",
                            "The challenge service returned a cookie for an unexpected domain.",
                        )
                self._authenticated = True
                return

        raise AuthenticationError(
            "AUTH_REDIRECT_REJECTED",
            "The authentication flow exceeded the redirect limit.",
        )

    def _iter_limited_body(self, response: Response) -> Iterator[bytes]:
        total = 0
        for chunk in response.iter_content(chunk_size=64 * 1024):
            if not chunk:
                continue
            total += len(chunk)
            if total > self._settings.max_response_bytes:
                raise ValidationError(
                    "SCHEMA_MISMATCH",
                    "The challenge response exceeded the configured size limit.",
                    next_action="Stop the case and inspect the remote response contract.",
                )
            yield chunk

    def _decode_json(self, response: Response, *, mutation: bool) -> dict[str, Any]:
        content_type = response.headers.get("Content-Type", "").split(";", 1)[0].strip()
        uncertain_mutation = mutation and not 400 <= response.status_code < 500
        if content_type != "application/json":
            error_cls = DeliveryUnknownError if uncertain_mutation else ValidationError
            raise error_cls(
                "DELIVERY_UNKNOWN" if error_cls is DeliveryUnknownError else "SCHEMA_MISMATCH",
                (
                    "The state-changing request returned an unexpected response format."
                    if error_cls is DeliveryUnknownError
                    else "The challenge API returned an unexpected content type."
                ),
                delivery_status="UNKNOWN" if error_cls is DeliveryUnknownError else "NOT_SENT",
                next_action=(
                    "Do not retry. Reconcile the challenge state first."
                    if error_cls is DeliveryUnknownError
                    else "Stop and inspect the interface contract."
                ),
            )
        try:
            raw = b"".join(self._iter_limited_body(response))
            value = json.loads(raw.decode("utf-8"))
        except DeliveryUnknownError:
            raise
        except (UnicodeDecodeError, json.JSONDecodeError, ValidationError) as exc:
            if uncertain_mutation:
                raise DeliveryUnknownError(
                    "DELIVERY_UNKNOWN",
                    "The state-changing request returned an unreadable response.",
                    delivery_status="UNKNOWN",
                    next_action="Do not retry. Reconcile the challenge state first.",
                ) from exc
            raise ValidationError(
                "SCHEMA_MISMATCH",
                "The challenge API returned invalid JSON.",
                next_action="Stop and inspect the interface contract.",
            ) from exc
        if not isinstance(value, dict):
            if uncertain_mutation:
                raise DeliveryUnknownError(
                    "DELIVERY_UNKNOWN",
                    "The state-changing request returned an invalid JSON shape.",
                    delivery_status="UNKNOWN",
                    next_action="Do not retry. Reconcile the challenge state first.",
                )
            raise ValidationError(
                "SCHEMA_MISMATCH",
                "The challenge API returned a non-object JSON response.",
            )
        return value

    def _safe_remote_detail(self, data: dict[str, Any], fallback: str) -> str:
        value = data.get("detail", data.get("message", fallback))
        if not isinstance(value, str):
            value = fallback
        return redact_text(value[:1000], additional_secrets=(self._settings.user_id,))

    def _request_json(
        self,
        method: str,
        path: str,
        *,
        payload: dict[str, Any] | None = None,
        mutation: bool,
    ) -> tuple[int, dict[str, Any]]:
        if not self._authenticated:
            raise AuthenticationError(
                "AUTH_MISSING",
                "The challenge session has not been opened.",
                next_action="Call glacier_open_session first.",
            )
        headers = {"Accept": "application/json", "Referer": ""}
        if payload is not None:
            headers["Content-Type"] = "application/json"
        try:
            response = self._session.request(
                method,
                self._url_for_path(path),
                json=payload,
                allow_redirects=False,
                headers=headers,
                stream=True,
                timeout=self._timeout,
            )
        except (requests.Timeout, requests.ConnectionError) as exc:
            if mutation:
                raise DeliveryUnknownError(
                    "DELIVERY_UNKNOWN",
                    "The state-changing request has an unknown delivery outcome.",
                    delivery_status="UNKNOWN",
                    next_action="Do not retry. Reconcile the challenge state first.",
                ) from exc
            raise RemoteRejectedError(
                "REMOTE_UNAVAILABLE",
                "The challenge service could not be reached.",
                retryable=True,
                next_action="Verify VPN connectivity and retry the read-only operation.",
            ) from exc
        except requests.RequestException as exc:
            if mutation:
                raise DeliveryUnknownError(
                    "DELIVERY_UNKNOWN",
                    "The state-changing request has an unknown delivery outcome.",
                    delivery_status="UNKNOWN",
                    next_action="Do not retry. Reconcile the challenge state first.",
                ) from exc
            raise RemoteRejectedError(
                "REMOTE_UNAVAILABLE",
                "The challenge service request failed.",
                retryable=True,
            ) from exc

        with response:
            if response.status_code in {301, 302, 303, 307, 308}:
                error_cls = DeliveryUnknownError if mutation else AuthenticationError
                raise error_cls(
                    "DELIVERY_UNKNOWN" if mutation else "SESSION_EXPIRED",
                    (
                        "The state-changing request returned an unexpected redirect."
                        if mutation
                        else "The challenge session is no longer authenticated."
                    ),
                    delivery_status="UNKNOWN" if mutation else "NOT_SENT",
                    next_action=(
                        "Do not retry. Reconcile the challenge state first."
                        if mutation
                        else "Open a new session only when no case is active."
                    ),
                )

            data = self._decode_json(response, mutation=mutation)
            status = response.status_code

        if status >= 500:
            if mutation:
                raise DeliveryUnknownError(
                    "DELIVERY_UNKNOWN",
                    "The state-changing request returned a server error with unknown outcome.",
                    delivery_status="UNKNOWN",
                    next_action="Do not retry. Reconcile the challenge state first.",
                )
            raise RemoteRejectedError(
                "REMOTE_UNAVAILABLE",
                "The challenge service returned a server error.",
                retryable=True,
                http_status=status,
            )
        if status == 429:
            raise RemoteRejectedError(
                "REMOTE_RATE_LIMITED",
                "The challenge service rate-limited the request.",
                retryable=True,
                delivery_status="REJECTED",
                next_action="Wait before starting another approved operation; do not auto-retry.",
                http_status=status,
            )
        if status in {401, 403}:
            raise AuthenticationError(
                "SESSION_EXPIRED",
                "The challenge session is not authorized.",
                delivery_status="REJECTED" if mutation else "NOT_SENT",
                next_action="Do not reauthenticate while a case may still be active.",
            )
        return status, data

    def get_openapi(self) -> dict[str, Any]:
        status, data = self._request_json("GET", "/openapi.json", mutation=False)
        if status != 200:
            raise RemoteRejectedError(
                "REMOTE_REJECTED",
                self._safe_remote_detail(data, "The OpenAPI schema request was rejected."),
                delivery_status="REJECTED",
                http_status=status,
            )
        paths = data.get("paths")
        if not isinstance(paths, dict) or not _REQUIRED_API_PATHS.issubset(paths):
            raise ValidationError(
                "SCHEMA_MISMATCH",
                "The challenge OpenAPI schema is missing required endpoints.",
                next_action="Stop live execution until the interface contract is reviewed.",
            )
        return data

    def get_pending(self) -> PendingResult:
        status, data = self._request_json("GET", "/api/pending-evaluation", mutation=False)
        if status != 200:
            raise RemoteRejectedError(
                "REMOTE_REJECTED",
                self._safe_remote_detail(data, "The pending-state request was rejected."),
                delivery_status="REJECTED",
                http_status=status,
            )
        pending = data.get("pending")
        if not isinstance(pending, bool):
            raise ValidationError(
                "SCHEMA_MISMATCH",
                "The pending-state response is missing a boolean pending field.",
            )
        if not pending:
            return PendingResult(pending=False)

        nested_draft = data.get("draft")
        if nested_draft is None:
            draft = data
        elif isinstance(nested_draft, dict):
            draft = nested_draft
        else:
            raise ValidationError(
                "SCHEMA_MISMATCH",
                "A pending evaluation did not include a recoverable draft.",
            )
        category_value = draft.get("category")
        subcategory_value = draft.get(
            "policy_subcategory",
            draft.get("policySubcategory", ""),
        )
        if not isinstance(category_value, str) or not isinstance(subcategory_value, str):
            raise ValidationError(
                "SCHEMA_MISMATCH",
                "The pending draft category fields are invalid.",
            )
        category, subcategory = validate_category(category_value, subcategory_value)
        raw_transcript = draft.get("transcript")
        if not isinstance(raw_transcript, list):
            raise ValidationError(
                "SCHEMA_MISMATCH",
                "The pending draft transcript is invalid.",
            )
        transcript = [
            turn
            for item in raw_transcript
            if (
                turn := TranscriptTurn.from_remote(
                    item,
                    category=category.value,
                    policy_subcategory=subcategory,
                )
            )
            is not None
        ]
        stop_reason = draft.get(
            "stop_reason",
            draft.get("stopReason", draft.get("detail", "")),
        )
        return PendingResult(
            pending=True,
            category=category.value,
            policy_subcategory=subcategory,
            transcript=transcript,
            stop_reason=stop_reason if isinstance(stop_reason, str) else "",
        )

    def chat(
        self,
        *,
        category: str,
        policy_subcategory: str,
        message: str,
    ) -> ChatResult:
        status, data = self._request_json(
            "POST",
            "/api/chat",
            payload={
                "category": category,
                "policy_subcategory": policy_subcategory,
                "message": message,
            },
            mutation=True,
        )
        pending_required = data.get("pending_required") is True
        if status >= 400 and not pending_required:
            raise RemoteRejectedError(
                "REMOTE_REJECTED",
                self._safe_remote_detail(data, "The chat request was rejected."),
                delivery_status="REJECTED",
                http_status=status,
            )

        raw_transcript = data.get("transcript")
        transcript: list[TranscriptTurn] | None = None
        if raw_transcript is not None:
            if not isinstance(raw_transcript, list):
                raise DeliveryUnknownError(
                    "DELIVERY_UNKNOWN",
                    "The chat response contained an invalid authoritative transcript.",
                    delivery_status="UNKNOWN",
                    next_action="Do not retry. Reconcile the pending state.",
                )
            transcript = [
                turn
                for item in raw_transcript
                if (
                    turn := TranscriptTurn.from_remote(
                        item,
                        category=category,
                        policy_subcategory=policy_subcategory,
                    )
                )
                is not None
            ]

        reply = data.get("reply", data.get("detail", ""))
        if not isinstance(reply, str) or not reply.strip():
            if pending_required:
                reply = str(data.get("stop_reason") or "Blocked by Guardrails")
            else:
                raise DeliveryUnknownError(
                    "DELIVERY_UNKNOWN",
                    "The chat response did not include a usable reply.",
                    delivery_status="UNKNOWN",
                    next_action="Do not retry. Reconcile the challenge state first.",
                )
        stop_reason = data.get("stop_reason", data.get("detail", ""))
        return ChatResult(
            reply=reply,
            pending_required=pending_required,
            transcript=transcript,
            stop_reason=stop_reason if isinstance(stop_reason, str) else "",
            http_status=status,
        )

    def clear(self) -> dict[str, Any]:
        status, data = self._request_json("POST", "/api/clear_chat", mutation=True)
        if status >= 400:
            raise RemoteRejectedError(
                "REMOTE_REJECTED",
                self._safe_remote_detail(data, "The clear-chat request was rejected."),
                delivery_status="REJECTED",
                http_status=status,
            )
        return data

    def evaluate(self, payload: dict[str, Any]) -> SubmissionReceipt:
        status, data = self._request_json(
            "POST",
            "/api/evaluation",
            payload=payload,
            mutation=True,
        )
        if status >= 400:
            raise RemoteRejectedError(
                "REMOTE_REJECTED",
                self._safe_remote_detail(data, "The evaluation submission was rejected."),
                delivery_status="REJECTED",
                http_status=status,
            )
        evaluation_id = data.get("evaluation_id")
        conversation_id = data.get("conversation_id")
        if not isinstance(evaluation_id, str) or not evaluation_id.strip():
            raise DeliveryUnknownError(
                "DELIVERY_UNKNOWN",
                "The submission response did not include an evaluation receipt.",
                delivery_status="UNKNOWN",
                next_action="Do not retry the submission. Reconcile it manually.",
            )
        if not isinstance(conversation_id, str) or not conversation_id.strip():
            raise DeliveryUnknownError(
                "DELIVERY_UNKNOWN",
                "The submission response did not include a conversation receipt.",
                delivery_status="UNKNOWN",
                next_action="Do not retry the submission. Reconcile it manually.",
            )
        message = data.get("message", "Evaluation saved.")
        return SubmissionReceipt(
            message=message if isinstance(message, str) else "Evaluation saved.",
            evaluation_id=evaluation_id,
            conversation_id=conversation_id,
        )

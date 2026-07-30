from __future__ import annotations

import json
from collections import deque
from typing import Any

import pytest
import requests
from requests.cookies import RequestsCookieJar

from project_glacier_mcp.errors import (
    AuthenticationError,
    DeliveryUnknownError,
)
from project_glacier_mcp.settings import EXPECTED_ORIGIN
from project_glacier_mcp.transport import RequestsChallengeTransport


class FakeResponse:
    def __init__(
        self,
        status_code: int,
        *,
        body: bytes = b"",
        headers: dict[str, str] | None = None,
    ) -> None:
        self.status_code = status_code
        self._body = body
        self.headers = headers or {}

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def iter_content(self, chunk_size: int):
        for start in range(0, len(self._body), chunk_size):
            yield self._body[start : start + chunk_size]


class FakeSession:
    def __init__(self, responses: list[FakeResponse | Exception]) -> None:
        self.responses = deque(responses)
        self.headers: dict[str, str] = {}
        self.cookies = RequestsCookieJar()
        self.trust_env = True
        self.calls: list[dict[str, Any]] = []

    def request(self, method: str, url: str, **kwargs):
        self.calls.append({"method": method, "url": url, **kwargs})
        response = self.responses.popleft()
        if isinstance(response, Exception):
            raise response
        return response

    def get(self, url: str, **kwargs):
        return self.request("GET", url, **kwargs)


def json_response(status: int, value: Any) -> FakeResponse:
    return FakeResponse(
        status,
        body=json.dumps(value).encode(),
        headers={"Content-Type": "application/json"},
    )


def authenticated_transport(settings, extra_responses):
    jwt = "eyJabcdefghijk.abcdefghijkl.abcdefghijkl"
    session = FakeSession(
        [
            FakeResponse(302, headers={"Location": f"/evaluation?token={jwt}"}),
            FakeResponse(200, headers={"Content-Type": "text/html"}),
            *extra_responses,
        ]
    )
    session.cookies.set(
        "policy_verifier_mockup_auth",
        "secret-auth-cookie",
        domain="144.25.123.139",
        path="/",
    )
    session.cookies.set(
        "policy_verifier_mockup_chat",
        "secret-chat-cookie",
        domain="144.25.123.139",
        path="/",
    )
    transport = RequestsChallengeTransport(settings, session=session)
    transport.authenticate()
    return transport, session, jwt


def test_authentication_follows_only_same_origin_and_ignores_proxy_environment(
    settings_factory,
):
    transport, session, jwt = authenticated_transport(settings_factory(), [])

    assert transport.authenticated is True
    assert session.trust_env is False
    assert len(session.calls) == 2
    assert session.calls[0]["allow_redirects"] is False
    assert session.calls[1]["allow_redirects"] is False
    assert session.calls[1]["headers"]["Referer"] == ""
    assert jwt in session.calls[1]["url"]


def test_authentication_rejects_cross_origin_redirect_without_leaking_token(
    settings_factory,
):
    token = "eyJabcdefghijk.abcdefghijkl.abcdefghijkl"
    session = FakeSession(
        [FakeResponse(302, headers={"Location": f"http://evil.example/x?token={token}"})]
    )
    transport = RequestsChallengeTransport(settings_factory(), session=session)

    with pytest.raises(AuthenticationError) as caught:
        transport.authenticate()

    assert caught.value.code == "AUTH_REDIRECT_REJECTED"
    assert token not in str(caught.value)
    assert "evil.example" not in str(caught.value)


def test_pending_state_is_typed_and_read_only(settings_factory):
    transport, session, _jwt = authenticated_transport(
        settings_factory(),
        [json_response(200, {"pending": False})],
    )

    result = transport.get_pending()

    assert result.pending is False
    assert session.calls[-1]["method"] == "GET"
    assert session.calls[-1]["url"] == f"{EXPECTED_ORIGIN}/api/pending-evaluation"


@pytest.mark.parametrize("nested", [True, False])
def test_pending_state_accepts_nested_or_top_level_draft(settings_factory, nested):
    draft = {
        "category": "Business Rules Verifier",
        "policySubcategory": "IAM and Authorization",
        "transcript": [
            {"role": "user", "text": "synthetic test"},
            {"role": "assistant", "text": "Blocked by Guardrails"},
        ],
        "stopReason": "Blocked by Guardrails",
    }
    response = {"pending": True, "draft": draft} if nested else {"pending": True, **draft}
    transport, _session, _jwt = authenticated_transport(
        settings_factory(),
        [json_response(200, response)],
    )

    result = transport.get_pending()

    assert result.pending is True
    assert result.category == "Business Rules Verifier"
    assert result.policy_subcategory == "IAM and Authorization"
    assert result.stop_reason == "Blocked by Guardrails"
    assert [turn.role for turn in result.transcript] == ["user", "assistant"]


def test_chat_success_and_pending_transcript_are_parsed(settings_factory):
    success = {
        "reply": "A safe response",
    }
    blocked = {
        "pending_required": True,
        "detail": "Blocked by Guardrails: test",
        "stop_reason": "Blocked by Guardrails: test",
        "transcript": [
            {"role": "user", "text": "test"},
            {"role": "assistant", "text": "Blocked by Guardrails: test"},
        ],
    }
    transport, _session, _jwt = authenticated_transport(
        settings_factory(),
        [json_response(200, success), json_response(409, blocked)],
    )

    first = transport.chat(
        category="Content Moderation",
        policy_subcategory="",
        message="hello",
    )
    second = transport.chat(
        category="Content Moderation",
        policy_subcategory="",
        message="test",
    )

    assert first.reply == "A safe response"
    assert first.pending_required is False
    assert second.pending_required is True
    assert second.transcript is not None
    assert [turn.role for turn in second.transcript] == ["user", "assistant"]


@pytest.mark.parametrize(
    "response",
    [
        requests.Timeout("contains http://example.invalid/?token=secret"),
        FakeResponse(200, body=b"not-json", headers={"Content-Type": "text/plain"}),
        FakeResponse(
            200,
            body=b"not-json",
            headers={"Content-Type": "application/json"},
        ),
        json_response(500, {"detail": "server error"}),
    ],
)
def test_ambiguous_chat_results_are_delivery_unknown(settings_factory, response):
    transport, _session, _jwt = authenticated_transport(settings_factory(), [response])

    with pytest.raises(DeliveryUnknownError) as caught:
        transport.chat(
            category="Content Moderation",
            policy_subcategory="",
            message="test",
        )

    assert caught.value.delivery_status == "UNKNOWN"
    assert "token=secret" not in str(caught.value)

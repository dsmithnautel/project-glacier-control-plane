"""Broker configuration with fail-closed validation."""

from __future__ import annotations

import os
import re
import subprocess
import sys
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

from .errors import ConfigurationError

EXPECTED_ORIGIN = "http://144.25.123.139:8653"
DEFAULT_KEYCHAIN_SERVICE = "project-glacier-user-id"
_USER_ID = re.compile(r"\A[0-9]{6,20}\Z")


def _read_keychain(service: str) -> str | None:
    if sys.platform != "darwin":
        return None
    try:
        result = subprocess.run(
            [
                "/usr/bin/security",
                "find-generic-password",
                "-s",
                service,
                "-w",
            ],
            check=False,
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    value = result.stdout.strip()
    return value or None


def _parse_bool(value: str | None, *, default: bool) -> bool:
    if value is None:
        return default
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise ConfigurationError(
        "CONFIG_INVALID",
        "A boolean configuration value is invalid.",
        next_action="Use true/false, yes/no, on/off, or 1/0.",
    )


def _parse_int(
    value: str | None,
    *,
    default: int,
    minimum: int,
    maximum: int,
    name: str,
) -> int:
    if value is None:
        return default
    try:
        parsed = int(value)
    except ValueError as exc:
        raise ConfigurationError(
            "CONFIG_INVALID",
            f"{name} must be an integer.",
        ) from exc
    if not minimum <= parsed <= maximum:
        raise ConfigurationError(
            "CONFIG_INVALID",
            f"{name} must be between {minimum} and {maximum}.",
        )
    return parsed


@dataclass(frozen=True, slots=True)
class Settings:
    """Validated settings for one local broker process."""

    base_url: str
    user_id: str
    allow_submit: bool
    max_submissions: int
    connect_timeout_seconds: int
    read_timeout_seconds: int
    max_response_bytes: int
    max_message_chars: int
    max_summary_chars: int
    max_turns: int
    state_dir: Path

    @classmethod
    def from_environment(
        cls,
        environment: Mapping[str, str] | None = None,
        *,
        keychain_reader: Callable[[str], str | None] = _read_keychain,
    ) -> Settings:
        env = os.environ if environment is None else environment
        base_url = env.get("GLACIER_BASE_URL", EXPECTED_ORIGIN).rstrip("/")
        parsed = urlsplit(base_url)
        if (
            base_url != EXPECTED_ORIGIN
            or parsed.scheme != "http"
            or parsed.hostname != "144.25.123.139"
            or parsed.port != 8653
            or parsed.username is not None
            or parsed.password is not None
            or parsed.path not in {"", "/"}
            or parsed.query
            or parsed.fragment
        ):
            raise ConfigurationError(
                "CONFIG_INVALID",
                "GLACIER_BASE_URL must be the exact authorized challenge origin.",
                next_action=f"Set GLACIER_BASE_URL to {EXPECTED_ORIGIN}.",
            )

        service = env.get("GLACIER_KEYCHAIN_SERVICE", DEFAULT_KEYCHAIN_SERVICE).strip()
        user_id = env.get("GLACIER_USER_ID", "").strip()
        if not user_id and service:
            user_id = (keychain_reader(service) or "").strip()
        if not user_id:
            raise ConfigurationError(
                "AUTH_MISSING",
                "The Project Glacier participant credential is unavailable.",
                next_action=(
                    "Set GLACIER_USER_ID for the MCP process or store it in the configured "
                    "macOS Keychain service."
                ),
            )
        if not _USER_ID.fullmatch(user_id):
            raise ConfigurationError(
                "CONFIG_INVALID",
                "The Project Glacier participant credential has an invalid format.",
            )

        state_dir_value = env.get(
            "GLACIER_STATE_DIR",
            str(Path.home() / ".local" / "state" / "project-glacier-mcp"),
        )
        state_dir = Path(state_dir_value).expanduser()
        if not state_dir.is_absolute():
            raise ConfigurationError(
                "CONFIG_INVALID",
                "GLACIER_STATE_DIR must be an absolute path; relative paths can place state in "
                "the broker working directory.",
            )

        return cls(
            base_url=EXPECTED_ORIGIN,
            user_id=user_id,
            allow_submit=_parse_bool(env.get("GLACIER_ALLOW_SUBMIT"), default=False),
            max_submissions=_parse_int(
                env.get("GLACIER_MAX_SUBMISSIONS"),
                default=20,
                minimum=1,
                maximum=1000,
                name="GLACIER_MAX_SUBMISSIONS",
            ),
            connect_timeout_seconds=_parse_int(
                env.get("GLACIER_CONNECT_TIMEOUT_SECONDS"),
                default=10,
                minimum=1,
                maximum=60,
                name="GLACIER_CONNECT_TIMEOUT_SECONDS",
            ),
            read_timeout_seconds=_parse_int(
                env.get("GLACIER_READ_TIMEOUT_SECONDS"),
                default=180,
                minimum=5,
                maximum=600,
                name="GLACIER_READ_TIMEOUT_SECONDS",
            ),
            max_response_bytes=_parse_int(
                env.get("GLACIER_MAX_RESPONSE_BYTES"),
                default=4 * 1024 * 1024,
                minimum=1024,
                maximum=16 * 1024 * 1024,
                name="GLACIER_MAX_RESPONSE_BYTES",
            ),
            max_message_chars=_parse_int(
                env.get("GLACIER_MAX_MESSAGE_CHARS"),
                default=50_000,
                minimum=1,
                maximum=200_000,
                name="GLACIER_MAX_MESSAGE_CHARS",
            ),
            max_summary_chars=_parse_int(
                env.get("GLACIER_MAX_SUMMARY_CHARS"),
                default=20_000,
                minimum=1,
                maximum=100_000,
                name="GLACIER_MAX_SUMMARY_CHARS",
            ),
            max_turns=_parse_int(
                env.get("GLACIER_MAX_TURNS"),
                default=20,
                minimum=1,
                maximum=100,
                name="GLACIER_MAX_TURNS",
            ),
            state_dir=state_dir,
        )

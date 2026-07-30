from __future__ import annotations

from pathlib import Path

import pytest

from project_glacier_mcp.settings import EXPECTED_ORIGIN, Settings


@pytest.fixture
def settings_factory(tmp_path: Path):
    def factory(
        *,
        allow_submit: bool = False,
        state_name: str = "state",
        max_submissions: int = 5,
    ) -> Settings:
        return Settings(
            base_url=EXPECTED_ORIGIN,
            user_id="123456789",
            allow_submit=allow_submit,
            max_submissions=max_submissions,
            connect_timeout_seconds=1,
            read_timeout_seconds=5,
            max_response_bytes=1024 * 1024,
            max_message_chars=10_000,
            max_summary_chars=10_000,
            max_turns=10,
            state_dir=tmp_path / state_name,
        )

    return factory

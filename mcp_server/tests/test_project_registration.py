from __future__ import annotations

import os
import tomllib
from pathlib import Path

import pytest


def test_codex_registration_enables_bounded_zero_touch_and_is_secret_free():
    config_value = os.environ.get("PROJECT_GLACIER_CODEX_CONFIG")
    if config_value is None:
        pytest.skip("Set PROJECT_GLACIER_CODEX_CONFIG to test a consumer registration.")
    repository_root = Path(__file__).resolve().parents[1]
    config_path = Path(config_value).expanduser().resolve()
    config = tomllib.loads(config_path.read_text(encoding="utf-8"))
    server = config["mcp_servers"]["project_glacier"]

    assert server["required"] is True
    assert Path(server["command"]).expanduser() == repository_root / ".venv" / "bin" / "python"
    assert Path(server["cwd"]).resolve() == repository_root
    assert server["args"] == ["-m", "project_glacier_mcp.server"]
    assert server["default_tools_approval_mode"] == "prompt"

    environment = server["env"]
    assert environment["GLACIER_BASE_URL"] == "http://144.25.123.139:8653"
    assert environment["GLACIER_ALLOW_SUBMIT"] == "true"
    assert environment["GLACIER_KEYCHAIN_SERVICE"] == "project-glacier-user-id"
    assert int(environment["GLACIER_MAX_SUBMISSIONS"]) == 1000
    assert 1 <= int(environment["GLACIER_MAX_TURNS"]) <= 20
    assert "GLACIER_USER_ID" not in environment

    tools = server["tools"]
    assert {name: value["approval_mode"] for name, value in tools.items()} == {
        "glacier_open_session": "approve",
        "glacier_start_case": "approve",
        "glacier_send_message": "approve",
        "glacier_get_state": "approve",
        "glacier_prepare_submission": "approve",
        "glacier_commit_submission": "approve",
        "glacier_reset_case": "approve",
    }

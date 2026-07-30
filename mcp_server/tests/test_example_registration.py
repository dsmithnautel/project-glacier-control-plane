from __future__ import annotations

import tomllib
from pathlib import Path


def test_example_registration_is_manual_submission_disabled_and_secret_free():
    repository_root = Path(__file__).resolve().parents[1]
    config_path = repository_root / "config" / "codex.toml.example"
    config = tomllib.loads(config_path.read_text(encoding="utf-8"))
    server = config["mcp_servers"]["project_glacier"]

    assert server["required"] is True
    assert server["command"] == "/absolute/path/to/project-glacier-mcp/.venv/bin/python"
    assert server["cwd"] == "/absolute/path/to/project-glacier-mcp"
    assert server["args"] == ["-m", "project_glacier_mcp.server"]
    assert server["default_tools_approval_mode"] == "prompt"

    environment = server["env"]
    assert "GLACIER_BASE_URL" not in environment
    assert environment["GLACIER_ALLOW_SUBMIT"] == "false"
    assert environment["GLACIER_KEYCHAIN_SERVICE"] == "project-glacier-user-id"
    assert int(environment["GLACIER_MAX_SUBMISSIONS"]) == 20
    assert 1 <= int(environment["GLACIER_MAX_TURNS"]) <= 20
    assert environment["GLACIER_STATE_DIR"].startswith("/absolute/private/path/")
    assert "GLACIER_USER_ID" not in environment

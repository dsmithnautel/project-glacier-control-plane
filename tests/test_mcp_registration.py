from __future__ import annotations

import os
import tomllib
import unittest
from pathlib import Path


class MCPRegistrationIntegrationTests(unittest.TestCase):
    def test_generated_control_plane_registration_preserves_bounded_policy(self) -> None:
        config_value = os.environ.get("PROJECT_GLACIER_CODEX_CONFIG")
        if config_value is None:
            self.skipTest(
                "Set PROJECT_GLACIER_CODEX_CONFIG to test the generated consumer registration."
            )

        repository_root = Path(__file__).resolve().parents[1]
        mcp_root = repository_root / "mcp_server"
        config_path = Path(config_value).expanduser().resolve()
        with config_path.open("rb") as stream:
            config = tomllib.load(stream)
        server = config["mcp_servers"]["project_glacier"]

        self.assertIs(server["required"], True)
        self.assertEqual(
            Path(server["command"]).expanduser(),
            mcp_root / ".venv" / "bin" / "python",
        )
        self.assertEqual(Path(server["cwd"]).resolve(), mcp_root)
        self.assertEqual(server["args"], ["-m", "project_glacier_mcp.server"])
        self.assertEqual(server["default_tools_approval_mode"], "prompt")

        environment = server["env"]
        self.assertEqual(environment["GLACIER_BASE_URL"], "http://144.25.123.139:8653")
        self.assertEqual(environment["GLACIER_ALLOW_SUBMIT"], "true")
        self.assertEqual(environment["GLACIER_KEYCHAIN_SERVICE"], "project-glacier-user-id")
        self.assertEqual(int(environment["GLACIER_MAX_SUBMISSIONS"]), 1000)
        self.assertLessEqual(int(environment["GLACIER_MAX_TURNS"]), 20)
        self.assertGreaterEqual(int(environment["GLACIER_MAX_TURNS"]), 1)
        self.assertNotIn("GLACIER_USER_ID", environment)

        tools = server["tools"]
        self.assertEqual(
            {name: value["approval_mode"] for name, value in tools.items()},
            {
                "glacier_open_session": "approve",
                "glacier_start_case": "approve",
                "glacier_send_message": "approve",
                "glacier_get_state": "approve",
                "glacier_prepare_submission": "approve",
                "glacier_commit_submission": "approve",
                "glacier_reset_case": "approve",
            },
        )


if __name__ == "__main__":
    unittest.main()

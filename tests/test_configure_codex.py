from __future__ import annotations

import os
import shlex
import shutil
import stat
import subprocess
import sys
import tempfile
import tomllib
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts import configure_codex

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
CONFIGURE_SCRIPT = REPOSITORY_ROOT / "scripts/configure_codex.py"
TEMPLATE_ROOT = REPOSITORY_ROOT / "config/codex"


class ConfigureCodexTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary_directory.cleanup)
        self.repo_root = Path(self.temporary_directory.name) / "project-glacier"
        self.repo_root.mkdir()
        shutil.copytree(TEMPLATE_ROOT, self.repo_root / "config/codex")

    def _create_mcp_python(self) -> Path:
        python = self.repo_root / "mcp_server/.venv/bin/python"
        python.parent.mkdir(parents=True, exist_ok=True)
        expected_cwd = shlex.quote(os.fspath(python.parent.resolve()))
        python.write_text(
            "#!/bin/sh\n"
            "test \"$#\" -eq 3 || exit 11\n"
            "test \"$1\" = '-I' || exit 12\n"
            "test \"$2\" = '-c' || exit 13\n"
            f"test \"$(pwd -P)\" = {expected_cwd} || exit 14\n"
            "case \"$3\" in\n"
            "  *project_glacier_mcp.server*) ;;\n"
            "  *) exit 15 ;;\n"
            "esac\n"
            "printf 'PROJECT_GLACIER_MCP_IMPORT_OK\\n'\n",
            encoding="utf-8",
        )
        python.chmod(0o700)
        return python

    def _run(self, *arguments: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [
                sys.executable,
                os.fspath(CONFIGURE_SCRIPT),
                "--repo-root",
                os.fspath(self.repo_root),
                *arguments,
            ],
            check=False,
            capture_output=True,
            text=True,
        )

    def _generated_paths(self) -> tuple[Path, Path, Path]:
        return (
            self.repo_root / ".codex/config.toml",
            self.repo_root / ".codex/agents/capacity-bottleneck-optimizer.toml",
            self.repo_root / ".codex/agents/mcp-interface-reliability.toml",
        )

    def test_renders_all_files_with_absolute_local_paths_and_mode_0600(self) -> None:
        mcp_python = self._create_mcp_python()

        result = self._run()

        self.assertEqual(result.returncode, 0, result.stderr)
        mcp_root = self.repo_root / "mcp_server"
        state_dir = self.repo_root / ".glacier-state"
        for generated in self._generated_paths():
            self.assertTrue(generated.is_file())
            contents = generated.read_text(encoding="utf-8")
            self.assertIn(os.fspath(mcp_python), contents)
            self.assertIn(os.fspath(mcp_root), contents)
            self.assertNotIn("{{", contents)
            self.assertEqual(stat.S_IMODE(generated.stat().st_mode), 0o600)
            with generated.open("rb") as file:
                tomllib.load(file)

        parsed_configs = []
        for generated in self._generated_paths():
            with generated.open("rb") as stream:
                parsed_configs.append(tomllib.load(stream))
        for parsed in parsed_configs:
            server = parsed["mcp_servers"]["project_glacier"]
            self.assertEqual(server["command"], os.fspath(mcp_python))
            self.assertEqual(server["cwd"], os.fspath(mcp_root))

        root_config = self._generated_paths()[0].read_text(encoding="utf-8")
        reliability_config = self._generated_paths()[2].read_text(encoding="utf-8")
        self.assertIn(os.fspath(state_dir), root_config)
        self.assertIn(os.fspath(state_dir), reliability_config)
        self.assertNotIn("GLACIER_USER_ID", root_config)
        self.assertIn('default_tools_approval_mode = "prompt"', root_config)
        self.assertIn('GLACIER_ALLOW_SUBMIT = "false"', reliability_config)
        self.assertIn(
            'enabled_tools = ["glacier_open_session", "glacier_get_state"]',
            reliability_config,
        )

    def test_idempotent_render_succeeds_without_force(self) -> None:
        self._create_mcp_python()
        first = self._run()
        second = self._run()

        self.assertEqual(first.returncode, 0, first.stderr)
        self.assertEqual(second.returncode, 0, second.stderr)
        self.assertEqual(second.stdout.count("unchanged"), 3)

    def test_differing_file_requires_force(self) -> None:
        self._create_mcp_python()
        self.assertEqual(self._run().returncode, 0)
        config = self._generated_paths()[0]
        config.write_text("# local difference\n", encoding="utf-8")

        refused = self._run()

        self.assertEqual(refused.returncode, 2)
        self.assertIn("refusing to overwrite differing file", refused.stderr)
        self.assertEqual(config.read_text(encoding="utf-8"), "# local difference\n")

        forced = self._run("--force")
        self.assertEqual(forced.returncode, 0, forced.stderr)
        self.assertNotEqual(config.read_text(encoding="utf-8"), "# local difference\n")
        self.assertEqual(stat.S_IMODE(config.stat().st_mode), 0o600)

    def test_preflight_prevents_partial_creation_when_a_later_file_differs(self) -> None:
        self._create_mcp_python()
        root_config, capacity_config, _reliability_config = self._generated_paths()
        capacity_config.parent.mkdir(parents=True)
        capacity_config.write_text("# preserve local file\n", encoding="utf-8")

        result = self._run()

        self.assertEqual(result.returncode, 2)
        self.assertIn("refusing to overwrite differing file", result.stderr)
        self.assertFalse(root_config.exists())
        self.assertEqual(
            capacity_config.read_text(encoding="utf-8"),
            "# preserve local file\n",
        )

    def test_check_requires_the_configured_mcp_python(self) -> None:
        mcp_python = self._create_mcp_python()
        self.assertEqual(self._run().returncode, 0)
        mcp_python.unlink()

        checked = self._run("--check")

        self.assertEqual(checked.returncode, 2)
        self.assertIn("MCP Python does not exist", checked.stderr)

        self._create_mcp_python()
        checked = self._run("--check")
        self.assertEqual(checked.returncode, 0, checked.stderr)
        self.assertEqual(checked.stdout.count(": valid"), 3)

        config = self._generated_paths()[0]
        config.write_text("# drift\n", encoding="utf-8")
        drifted = self._run("--check")
        self.assertEqual(drifted.returncode, 2)
        self.assertIn("differs", drifted.stderr)

    def test_non_check_requires_executable_mcp_python(self) -> None:
        missing = self._run()
        self.assertEqual(missing.returncode, 2)
        self.assertIn("MCP Python does not exist", missing.stderr)

        python = self._create_mcp_python()
        python.chmod(0o600)
        not_executable = self._run()
        self.assertEqual(not_executable.returncode, 2)
        self.assertIn("MCP Python is not executable", not_executable.stderr)

    def test_rejects_an_executable_that_cannot_import_the_broker(self) -> None:
        python = self._create_mcp_python()
        python.write_bytes(b"#!/bin/sh\nexit 0\n")

        result = self._run()

        self.assertEqual(result.returncode, 2)
        self.assertIn("cannot import project_glacier_mcp.server", result.stderr)

    @unittest.skipUnless(hasattr(os, "symlink"), "symlinks are unavailable")
    def test_rejects_symlinked_mcp_tree_before_executing_it(self) -> None:
        external = self.repo_root.parent / "external-mcp"
        python = external / ".venv/bin/python"
        python.parent.mkdir(parents=True)
        sentinel = self.repo_root.parent / "import-probe-ran"
        python.write_text(
            "#!/bin/sh\n"
            f"touch {shlex.quote(os.fspath(sentinel))}\n"
            "printf 'PROJECT_GLACIER_MCP_IMPORT_OK\\n'\n",
            encoding="utf-8",
        )
        python.chmod(0o700)
        (self.repo_root / "mcp_server").symlink_to(
            external,
            target_is_directory=True,
        )

        result = self._run()

        self.assertEqual(result.returncode, 2)
        self.assertIn("destination parent must not contain symlinks", result.stderr)
        self.assertFalse(sentinel.exists())

    def test_rejects_replaced_repository_before_executing_new_tree(self) -> None:
        self._create_mcp_python()
        replacement = self.repo_root.parent / "replacement-tree"
        malicious_python = replacement / "mcp_server/.venv/bin/python"
        malicious_python.parent.mkdir(parents=True)
        sentinel = self.repo_root.parent / "replacement-tree-executed"
        malicious_python.write_text(
            "#!/bin/sh\n"
            f"touch {shlex.quote(os.fspath(sentinel))}\n"
            "printf 'PROJECT_GLACIER_MCP_IMPORT_OK\\n'\n",
            encoding="utf-8",
        )
        malicious_python.chmod(0o700)
        moved = self.repo_root.parent / "original-tree"
        real_open_root = configure_codex._open_root

        def replace_after_open(path: Path) -> int:
            descriptor = real_open_root(path)
            path.rename(moved)
            replacement.rename(path)
            return descriptor

        with (
            patch.object(
                configure_codex,
                "_open_root",
                side_effect=replace_after_open,
            ),
            self.assertRaisesRegex(
                configure_codex.ConfigurationError,
                "identity changed",
            ),
        ):
            configure_codex.configure(self.repo_root)

        self.assertFalse(sentinel.exists())

    @unittest.skipUnless(hasattr(os, "symlink"), "symlinks are unavailable")
    def test_refuses_symlink_destination_even_with_force(self) -> None:
        self._create_mcp_python()
        codex = self.repo_root / ".codex"
        codex.mkdir()
        sentinel = self.repo_root / "sentinel.toml"
        sentinel.write_text("preserve me\n", encoding="utf-8")
        (codex / "config.toml").symlink_to(sentinel)

        result = self._run("--force")

        self.assertEqual(result.returncode, 2)
        self.assertIn("refusing symlink destination", result.stderr)
        self.assertEqual(sentinel.read_text(encoding="utf-8"), "preserve me\n")

    @unittest.skipUnless(hasattr(os, "symlink"), "symlinks are unavailable")
    def test_refuses_symlink_destination_parent(self) -> None:
        self._create_mcp_python()
        external = self.repo_root / "external"
        external.mkdir()
        (self.repo_root / ".codex").symlink_to(external, target_is_directory=True)

        result = self._run()

        self.assertEqual(result.returncode, 2)
        self.assertIn("destination parent must not contain symlinks", result.stderr)
        self.assertEqual(list(external.iterdir()), [])

    def test_refuses_group_or_other_writable_destination_parent(self) -> None:
        self._create_mcp_python()
        codex = self.repo_root / ".codex"
        codex.mkdir()
        codex.chmod(0o777)

        result = self._run()

        self.assertEqual(result.returncode, 2)
        self.assertIn(
            "destination parent must not be writable by group or other users",
            result.stderr,
        )
        self.assertFalse((codex / "config.toml").exists())

    def test_refuses_hard_linked_destination(self) -> None:
        self._create_mcp_python()
        self.assertEqual(self._run().returncode, 0)
        config = self._generated_paths()[0]
        outside = self.repo_root / "outside-config.toml"
        os.link(config, outside)

        result = self._run()

        self.assertEqual(result.returncode, 2)
        self.assertIn("destination must not have hard links", result.stderr)
        self.assertEqual(config.stat().st_ino, outside.stat().st_ino)

    @unittest.skipUnless(hasattr(os, "symlink"), "symlinks are unavailable")
    def test_refuses_symlinked_template(self) -> None:
        self._create_mcp_python()
        template = self.repo_root / "config/codex/config.toml.template"
        external = self.repo_root / "external-template.toml"
        external.write_text("safe = true\n", encoding="utf-8")
        template.unlink()
        template.symlink_to(external)

        result = self._run()

        self.assertEqual(result.returncode, 2)
        self.assertIn("refusing symlink destination", result.stderr)
        self.assertTrue(all(not path.exists() for path in self._generated_paths()))

    def test_check_rejects_insecure_file_permissions(self) -> None:
        self._create_mcp_python()
        self.assertEqual(self._run().returncode, 0)
        config = self._generated_paths()[0]
        config.chmod(0o644)

        result = self._run("--check")

        self.assertEqual(result.returncode, 2)
        self.assertIn("must have mode 0600", result.stderr)

    def test_check_and_force_are_mutually_exclusive(self) -> None:
        result = self._run("--check", "--force")
        self.assertEqual(result.returncode, 2)
        self.assertIn("--check and --force cannot be used together", result.stderr)

    def test_invalid_rendered_toml_is_rejected_before_any_write(self) -> None:
        self._create_mcp_python()
        template = self.repo_root / "config/codex/config.toml.template"
        template.write_text("broken = [\n", encoding="utf-8")

        result = self._run()

        self.assertEqual(result.returncode, 2)
        self.assertIn("rendered template is invalid TOML", result.stderr)
        self.assertTrue(all(not path.exists() for path in self._generated_paths()))

    def test_unusual_path_is_encoded_without_reprocessing_inserted_tokens(self) -> None:
        unusual_root = self.repo_root.parent / "project-{{MCP_ROOT}}-\x7f"
        self.repo_root.rename(unusual_root)
        self.repo_root = unusual_root
        mcp_python = self._create_mcp_python()

        result = self._run()

        self.assertEqual(result.returncode, 0, result.stderr)
        for generated in self._generated_paths():
            with generated.open("rb") as stream:
                parsed = tomllib.load(stream)
            server = parsed["mcp_servers"]["project_glacier"]
            self.assertEqual(server["command"], os.fspath(mcp_python))
            self.assertEqual(
                server["cwd"],
                os.fspath(self.repo_root / "mcp_server"),
            )


if __name__ == "__main__":
    unittest.main()

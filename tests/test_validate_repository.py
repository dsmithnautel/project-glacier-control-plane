from __future__ import annotations

import io
import os
import subprocess
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

from scripts import validate_repository


class RepositoryFixture:
    def __init__(self, root: Path) -> None:
        self.root = root

    def write(self, relative_path: str, content: str = "reusable\n") -> None:
        destination = self.root / relative_path
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(content, encoding="utf-8")

    def populate(self) -> None:
        subprocess.run(
            ["git", "init", "--quiet", self.root],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        for relative_path in validate_repository.EXPECTED_FILES:
            content = (
                "0123456789abcdef0123456789abcdef01234567\n"
                if relative_path in {"MCP_SOURCE_REVISION", "MCP_SOURCE_TREE"}
                else "reusable\n"
            )
            self.write(relative_path, content)
        self.write(
            ".gitignore",
            "\n".join(
                (
                    "/.codex/config.toml",
                    "/.codex/agents/*.toml",
                    "/.glacier-state/",
                    "/.project-glacier-orchestrator/",
                    "/CURRENT_CHECKPOINT.md",
                    "/PROJECT_GLACIER_RESUME.txt",
                    "/glacier_artifact_index.md",
                    "/glacier_coverage_matrix.md",
                    "/glacier_experiment_ledger.md",
                    "/glacier_findings_queue.md",
                    "/glacier_hypothesis_registry.md",
                    "/glacier_rules_dossier.md",
                    "/tmp/",
                    "**/.venv/",
                    "**/__pycache__/",
                    "**/.pytest_cache/",
                    "**/.ruff_cache/",
                    "",
                )
            ),
        )


class ValidateRepositoryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary_directory.cleanup)
        self.root = Path(self.temporary_directory.name)
        self.fixture = RepositoryFixture(self.root)
        self.fixture.populate()

    def validate(self) -> tuple[str, ...]:
        _root, _candidates, issues = validate_repository.validate_repository(self.root)
        return tuple(f"{issue.path}: {issue.rule}" for issue in issues)

    def test_valid_reusable_repository_passes(self) -> None:
        self.assertEqual(self.validate(), ())

    def test_ambient_git_routing_variables_cannot_redirect_validation(self) -> None:
        with tempfile.TemporaryDirectory() as decoy_name:
            decoy = Path(decoy_name)
            subprocess.run(
                ["git", "init", "--quiet", decoy],
                check=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            redirected = {
                "GIT_DIR": str(decoy / ".git"),
                "GIT_WORK_TREE": str(decoy),
                "GIT_INDEX_FILE": str(decoy / ".git/index"),
            }
            with patch.dict(os.environ, redirected):
                discovered = validate_repository.discover_repository(self.root)

        self.assertEqual(discovered, self.root.resolve())

    def test_repository_fsmonitor_hook_is_disabled(self) -> None:
        hook = self.root / "fsmonitor-hook"
        sentinel = self.root / "fsmonitor-ran"
        hook.write_text(
            "#!/bin/sh\n"
            "touch fsmonitor-ran\n"
            "exit 0\n",
            encoding="utf-8",
        )
        hook.chmod(0o700)
        subprocess.run(
            ["git", "-C", self.root, "config", "core.fsmonitor", str(hook)],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

        validate_repository.candidate_paths(self.root)

        self.assertFalse(sentinel.exists())

    def test_ignored_live_state_is_not_opened_or_scanned(self) -> None:
        forbidden_values = (
            "/Users/" + "dereksmithnautel",
            "ocid1." + "tenancy.example",
        )
        self.fixture.write(
            "PROJECT_GLACIER_RESUME.txt",
            "\n".join(forbidden_values),
        )
        self.fixture.write(
            ".glacier-state/private.sqlite3",
            "\n".join(forbidden_values),
        )

        self.assertEqual(self.validate(), ())

    def test_tracked_live_artifact_is_rejected_even_when_ignored(self) -> None:
        self.fixture.write("glacier_experiment_ledger.md", "live data\n")
        subprocess.run(
            ["git", "-C", self.root, "add", "--force", "glacier_experiment_ledger.md"],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

        self.assertIn(
            "glacier_experiment_ledger.md: live-populated-artifact",
            self.validate(),
        )

    def test_sensitive_content_is_redacted_from_diagnostics(self) -> None:
        machine_path = "/Users/" + "dereksmithnautel"
        tenancy_identifier = "ocid1." + "tenancy.example"
        self.fixture.write("README.md", f"{machine_path}\n{tenancy_identifier}\n")
        output = io.StringIO()

        with redirect_stdout(output):
            status = validate_repository.main(["--repo", str(self.root)])

        rendered = output.getvalue()
        self.assertEqual(status, 1)
        self.assertIn("README.md: machine-specific-path", rendered)
        self.assertIn("README.md: tenancy-identifier", rendered)
        self.assertNotIn(machine_path, rendered)
        self.assertNotIn(tenancy_identifier, rendered)

    def test_scans_staged_bytes_instead_of_a_clean_worktree_replacement(self) -> None:
        machine_path = "/Users/" + "dereksmithnautel"
        self.fixture.write("README.md", f"{machine_path}\n")
        subprocess.run(
            ["git", "-C", self.root, "add", "README.md"],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        self.fixture.write("README.md", "clean working copy\n")

        self.assertIn("README.md: machine-specific-path", self.validate())

    def test_oversized_staged_blob_is_rejected_before_blob_read(self) -> None:
        readme = self.root / "README.md"
        readme.write_bytes(b"x" * (validate_repository.MAX_CANDIDATE_BYTES + 1))
        subprocess.run(
            ["git", "-C", self.root, "add", "README.md"],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        real_run_git = validate_repository._run_git

        def reject_unbounded_blob_read(root: Path, arguments):
            if tuple(arguments[:2]) == ("cat-file", "blob"):
                self.fail("oversized blob bytes were requested")
            return real_run_git(root, arguments)

        with patch.object(
            validate_repository,
            "_run_git",
            side_effect=reject_unbounded_blob_read,
        ):
            issues = self.validate()

        self.assertIn("README.md: candidate-too-large", issues)

    def test_git_replacement_refs_cannot_hide_staged_sensitive_bytes(self) -> None:
        tenancy_identifier = "ocid1." + "tenancy.example"
        self.fixture.write("README.md", tenancy_identifier + "\n")
        subprocess.run(
            ["git", "-C", self.root, "add", "README.md"],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        unsafe_object = subprocess.run(
            ["git", "-C", self.root, "rev-parse", ":README.md"],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        clean_object = subprocess.run(
            ["git", "-C", self.root, "hash-object", "-w", "--stdin"],
            input="clean replacement\n",
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        subprocess.run(
            ["git", "-C", self.root, "replace", unsafe_object, clean_object],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

        self.assertIn("README.md: tenancy-identifier", self.validate())

    def test_rejects_a_staged_symlink_hidden_by_a_regular_worktree_file(self) -> None:
        readme = self.root / "README.md"
        readme.unlink()
        readme.symlink_to("SECURITY.md")
        subprocess.run(
            ["git", "-C", self.root, "add", "README.md"],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        readme.unlink()
        readme.write_text("clean working copy\n", encoding="utf-8")

        self.assertIn("README.md: symlink-not-inspected", self.validate())

    @unittest.skipUnless(hasattr(os, "symlink"), "symlinks are unavailable")
    def test_untracked_read_stays_on_pinned_ancestor_descriptor(self) -> None:
        original_directory = self.root / "container"
        original_directory.mkdir()
        (original_directory / "candidate.txt").write_text(
            "clean\n",
            encoding="utf-8",
        )
        moved_directory = self.root / "container-moved"
        external_directory = self.root / "external"
        external_directory.mkdir()
        (external_directory / "candidate.txt").write_text(
            "ocid1." + "tenancy.synthetic\n",
            encoding="utf-8",
        )
        real_open = os.open
        swapped = False

        def swap_before_leaf_open(path, flags, *args, **kwargs):
            nonlocal swapped
            if path == "candidate.txt" and not swapped:
                swapped = True
                original_directory.rename(moved_directory)
                original_directory.symlink_to(
                    external_directory,
                    target_is_directory=True,
                )
            return real_open(path, flags, *args, **kwargs)

        with patch.object(
            validate_repository.os,
            "open",
            side_effect=swap_before_leaf_open,
        ):
            issues = self.validate()

        self.assertNotIn("container/candidate.txt: tenancy-identifier", issues)
        self.assertTrue(swapped)

    def test_invalid_source_revision_is_rejected(self) -> None:
        self.fixture.write("MCP_SOURCE_REVISION", "ABC123\n")

        self.assertIn(
            "MCP_SOURCE_REVISION: invalid-source-revision",
            self.validate(),
        )

    def test_source_revision_accepts_crlf_portably(self) -> None:
        revision = self.root / "MCP_SOURCE_REVISION"
        revision.write_bytes(b"0123456789abcdef0123456789abcdef01234567\r\n")

        self.assertNotIn(
            "MCP_SOURCE_REVISION: invalid-source-revision",
            self.validate(),
        )

    def test_invalid_source_tree_is_rejected(self) -> None:
        self.fixture.write("MCP_SOURCE_TREE", "not-a-tree\n")

        self.assertIn(
            "MCP_SOURCE_TREE: invalid-source-tree",
            self.validate(),
        )

    def test_missing_reusable_file_is_rejected(self) -> None:
        (self.root / "SECURITY.md").unlink()

        self.assertIn("SECURITY.md: missing-reusable-file", self.validate())

    def test_missing_initializer_template_is_rejected(self) -> None:
        path = "templates/runtime/glacier_coverage_matrix.md"
        (self.root / path).unlink()

        self.assertIn(f"{path}: missing-reusable-file", self.validate())

    def test_staged_deletion_cannot_be_hidden_by_an_untracked_worktree_copy(self) -> None:
        subprocess.run(
            ["git", "-C", self.root, "add", "--all"],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        subprocess.run(
            ["git", "-C", self.root, "rm", "--cached", "SECURITY.md"],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

        self.assertIn("SECURITY.md: missing-reusable-file", self.validate())

    def test_index_change_during_validation_fails_closed(self) -> None:
        original_candidate_paths = validate_repository.candidate_paths

        def mutate_index(root: Path) -> tuple[str, ...]:
            candidates = original_candidate_paths(root)
            self.fixture.write("glacier_experiment_ledger.md", "late staged state\n")
            subprocess.run(
                [
                    "git",
                    "-C",
                    self.root,
                    "add",
                    "--force",
                    "glacier_experiment_ledger.md",
                ],
                check=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            return candidates

        with (
            patch.object(
                validate_repository,
                "candidate_paths",
                side_effect=mutate_index,
            ),
            self.assertRaisesRegex(
                validate_repository.ValidationError,
                "index changed",
            ),
        ):
            validate_repository.validate_repository(self.root)

    def test_protected_paths_are_casefolded_and_env_example_is_allowed(self) -> None:
        protected = (
            "project_glacier_resume.txt",
            ".CODEX/config.toml",
            "service/.ENV.local",
        )
        for path in protected:
            self.fixture.write(path, "runtime\n")
            subprocess.run(
                ["git", "-C", self.root, "add", "--force", path],
                check=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        self.fixture.write(".env.example", "NAME=placeholder\n")

        issues = self.validate()

        self.assertIn("project_glacier_resume.txt: live-runtime-file", issues)
        self.assertIn(".CODEX/config.toml: live-codex-configuration", issues)
        self.assertIn("service/.ENV.local: runtime-or-cache-path", issues)
        self.assertNotIn(".env.example: runtime-or-cache-path", issues)

    def test_protected_paths_are_unicode_compatibility_normalized(self) -> None:
        disguised = "CURRENT_CHEC\u212aPOINT.md"
        self.fixture.write(disguised, "runtime\n")
        subprocess.run(
            ["git", "-C", self.root, "add", "--force", disguised],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

        self.assertIn(f"{disguised}: live-runtime-file", self.validate())

    def test_repository_path_with_control_character_is_rejected(self) -> None:
        unsafe = self.root / "repository\n"
        unsafe.mkdir()
        subprocess.run(
            ["git", "init", "--quiet", unsafe],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

        with self.assertRaises(validate_repository.ValidationError):
            validate_repository.discover_repository(unsafe)

    def test_force_tracked_runtime_temporary_and_lock_files_are_rejected(self) -> None:
        paths = (
            ".PROJECT_GLACIER_RESUME.crash",
            ".project-glacier-init-crash",
            ".project-glacier-orchestrator.lock",
            ".codex/.config.toml.configure-codex-crash.tmp",
            ".codex/agents/.agent.toml.configure-codex-crash.tmp",
        )
        for path in paths:
            self.fixture.write(path, "runtime\n")
            subprocess.run(
                ["git", "-C", self.root, "add", "--force", path],
                check=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )

        issues = self.validate()
        for path in paths:
            self.assertIn(f"{path}: runtime-or-cache-path", issues)

    def test_generated_and_local_only_paths_are_rejected_when_force_tracked(self) -> None:
        paths = (
            ".DS_Store",
            "build/generated.bin",
            "dist/bundle.bin",
            "htmlcov/index.html",
            "package/example.egg-info/PKG-INFO",
            "cache.pyc",
            "cache.pyo",
        )
        for path in paths:
            self.fixture.write(path, "generated\n")
            subprocess.run(
                ["git", "-C", self.root, "add", "--force", path],
                check=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )

        issues = self.validate()
        for path in paths:
            self.assertIn(f"{path}: runtime-or-cache-path", issues)

    def test_live_codex_agent_configuration_is_rejected(self) -> None:
        self.fixture.write(".codex/agents/example.toml", "model = 'example'\n")
        subprocess.run(
            ["git", "-C", self.root, "add", "--force", ".codex/agents/example.toml"],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

        self.assertIn(
            ".codex/agents/example.toml: live-codex-agent-configuration",
            self.validate(),
        )

    def test_diagnostics_escape_control_characters_and_redact_sensitive_paths(self) -> None:
        injected = "odd\n- forged-result"
        sensitive = "ocid1." + "tenancy.synthetic"
        self.fixture.write(injected, "content\n")
        self.fixture.write(sensitive, sensitive + "\n")
        output = io.StringIO()

        with redirect_stdout(output):
            status = validate_repository.main(["--repo", str(self.root)])

        rendered = output.getvalue()
        self.assertEqual(status, 1)
        self.assertIn(r"odd\n- forged-result", rendered)
        self.assertNotIn("\n- forged-result:", rendered)
        self.assertIn("<redacted-path>", rendered)
        self.assertNotIn(sensitive, rendered)


if __name__ == "__main__":
    unittest.main()

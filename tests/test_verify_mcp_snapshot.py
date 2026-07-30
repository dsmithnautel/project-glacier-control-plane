from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path

from scripts import verify_mcp_snapshot


def _git(root: Path, *arguments: str) -> str:
    return subprocess.run(
        ["git", "-C", root, *arguments],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def _initialize_repository(root: Path) -> None:
    subprocess.run(
        ["git", "init", "--quiet", root],
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    _git(root, "config", "user.name", "Snapshot Test")
    _git(root, "config", "user.email", "snapshot-test@example.invalid")


class SnapshotFixture:
    def __init__(self, root: Path) -> None:
        self.source = root / "source"
        self.target = root / "target"
        self.source.mkdir()
        self.target.mkdir()
        _initialize_repository(self.source)
        _initialize_repository(self.target)

        (self.source / "README.md").write_text("canonical\n", encoding="utf-8")
        _git(self.source, "add", "README.md")
        _git(self.source, "commit", "--quiet", "-m", "Canonical source")
        self.revision = _git(self.source, "rev-parse", "HEAD")
        self.tree = _git(self.source, "rev-parse", "HEAD^{tree}")

        snapshot = self.target / "mcp_server"
        snapshot.mkdir()
        (snapshot / "README.md").write_text("canonical\n", encoding="utf-8")
        (self.target / "MCP_SOURCE_REVISION").write_text(
            self.revision + "\n",
            encoding="ascii",
        )
        (self.target / "MCP_SOURCE_TREE").write_text(
            self.tree + "\n",
            encoding="ascii",
        )
        _git(self.target, "add", ".")
        _git(self.target, "commit", "--quiet", "-m", "Pinned snapshot")


class VerifyMCPSnapshotTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary_directory.cleanup)
        self.fixture = SnapshotFixture(Path(self.temporary_directory.name))

    def test_offline_snapshot_tree_matches_pin(self) -> None:
        evidence = verify_mcp_snapshot.verify_snapshot(self.fixture.target)

        self.assertEqual(evidence.revision, self.fixture.revision)
        self.assertEqual(evidence.tree, self.fixture.tree)
        self.assertFalse(evidence.source_checked)

    def test_source_commit_and_tree_can_be_checked_without_network(self) -> None:
        evidence = verify_mcp_snapshot.verify_snapshot(
            self.fixture.target,
            source_repository=self.fixture.source,
        )

        self.assertTrue(evidence.source_checked)

    def test_dirty_vendored_tree_is_rejected(self) -> None:
        (self.fixture.target / "mcp_server" / "README.md").write_text(
            "drifted\n",
            encoding="utf-8",
        )

        with self.assertRaisesRegex(
            verify_mcp_snapshot.SnapshotVerificationError,
            "working tree differs",
        ):
            verify_mcp_snapshot.verify_snapshot(self.fixture.target)

    def test_invalid_pin_is_rejected_before_git_object_resolution(self) -> None:
        (self.fixture.target / "MCP_SOURCE_REVISION").write_text(
            "main\n",
            encoding="ascii",
        )

        with self.assertRaisesRegex(
            verify_mcp_snapshot.SnapshotVerificationError,
            "not a full lowercase Git object ID",
        ):
            verify_mcp_snapshot.verify_snapshot(self.fixture.target)

    def test_source_tree_mismatch_is_rejected(self) -> None:
        (self.fixture.source / "SECOND.md").write_text("different\n", encoding="utf-8")
        _git(self.fixture.source, "add", "SECOND.md")
        _git(self.fixture.source, "commit", "--quiet", "-m", "Different source tree")
        revision = _git(self.fixture.source, "rev-parse", "HEAD")
        (self.fixture.target / "MCP_SOURCE_REVISION").write_text(
            revision + "\n",
            encoding="ascii",
        )

        with self.assertRaisesRegex(
            verify_mcp_snapshot.SnapshotVerificationError,
            "commit tree does not match",
        ):
            verify_mcp_snapshot.verify_snapshot(
                self.fixture.target,
                source_repository=self.fixture.source,
            )


if __name__ == "__main__":
    unittest.main()

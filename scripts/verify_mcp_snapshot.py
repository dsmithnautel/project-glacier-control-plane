#!/usr/bin/env python3
"""Verify the vendored MCP subtree against its immutable standalone pin."""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

_GIT_OBJECT_RE = re.compile(r"[0-9a-f]{40}")
_REVISION_FILE = "MCP_SOURCE_REVISION"
_TREE_FILE = "MCP_SOURCE_TREE"
_SNAPSHOT_PATH = "mcp_server"


class SnapshotVerificationError(RuntimeError):
    """Raised when the standalone pin and vendored subtree cannot be proven equal."""


@dataclass(frozen=True)
class SnapshotEvidence:
    """Immutable evidence returned after successful snapshot verification."""

    revision: str
    tree: str
    source_checked: bool


def _run_git(root: Path, arguments: Sequence[str]) -> bytes:
    environment = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith("GIT_")
    }
    environment["GIT_OPTIONAL_LOCKS"] = "0"
    environment["GIT_NO_REPLACE_OBJECTS"] = "1"
    environment["LC_ALL"] = "C"
    result = subprocess.run(
        [
            "git",
            "--no-optional-locks",
            "-c",
            "core.fsmonitor=false",
            "-c",
            "core.hooksPath=/dev/null",
            "-c",
            "core.untrackedCache=false",
            "-C",
            os.fspath(root),
            *arguments,
        ],
        check=False,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        env=environment,
    )
    if result.returncode != 0:
        operation = arguments[0] if arguments else "repository"
        raise SnapshotVerificationError(f"Git {operation} verification failed")
    return result.stdout


def _discover_repository(start: Path) -> Path:
    try:
        canonical_start = start.expanduser().resolve(strict=True)
    except OSError as error:
        raise SnapshotVerificationError("the requested repository path is unavailable") from error
    if not canonical_start.is_dir():
        raise SnapshotVerificationError("the requested repository path is not a directory")

    raw_root = _run_git(canonical_start, ("rev-parse", "--show-toplevel"))
    decoded_root = os.fsdecode(raw_root.rstrip(b"\r\n"))
    if not decoded_root:
        raise SnapshotVerificationError("Git returned an empty worktree root")
    try:
        root = Path(decoded_root).resolve(strict=True)
    except OSError as error:
        raise SnapshotVerificationError("Git returned an unavailable worktree root") from error
    if not root.is_dir():
        raise SnapshotVerificationError("Git worktree root is not a directory")
    return root


def _read_pin(root: Path, name: str) -> str:
    try:
        value = (root / name).read_text(encoding="ascii").strip()
    except (OSError, UnicodeError) as error:
        raise SnapshotVerificationError(f"{name} is unavailable") from error
    if _GIT_OBJECT_RE.fullmatch(value) is None:
        raise SnapshotVerificationError(f"{name} is not a full lowercase Git object ID")
    return value


def _resolve_object(root: Path, expression: str) -> str:
    value = os.fsdecode(
        _run_git(root, ("rev-parse", "--verify", expression)).strip()
    )
    if _GIT_OBJECT_RE.fullmatch(value) is None:
        raise SnapshotVerificationError("Git returned an invalid object ID")
    return value


def verify_snapshot(
    repository: Path,
    *,
    source_repository: Path | None = None,
) -> SnapshotEvidence:
    """Prove that the committed vendored tree matches the recorded standalone tree."""

    root = _discover_repository(repository)
    revision = _read_pin(root, _REVISION_FILE)
    expected_tree = _read_pin(root, _TREE_FILE)

    dirty_snapshot = _run_git(
        root,
        (
            "status",
            "--porcelain=v1",
            "-z",
            "--untracked-files=all",
            "--",
            _SNAPSHOT_PATH,
        ),
    )
    if dirty_snapshot:
        raise SnapshotVerificationError(
            "the vendored MCP working tree differs from its committed snapshot"
        )

    actual_tree = _resolve_object(root, f"HEAD:{_SNAPSHOT_PATH}")
    if actual_tree != expected_tree:
        raise SnapshotVerificationError(
            "the committed vendored MCP tree does not match MCP_SOURCE_TREE"
        )

    source_checked = source_repository is not None
    if source_repository is not None:
        source_root = _discover_repository(source_repository)
        resolved_revision = _resolve_object(source_root, f"{revision}^{{commit}}")
        if resolved_revision != revision:
            raise SnapshotVerificationError(
                "the canonical source did not resolve to the pinned commit"
            )
        source_tree = _resolve_object(source_root, f"{revision}^{{tree}}")
        if source_tree != expected_tree:
            raise SnapshotVerificationError(
                "the pinned canonical commit tree does not match MCP_SOURCE_TREE"
            )

    return SnapshotEvidence(
        revision=revision,
        tree=expected_tree,
        source_checked=source_checked,
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Verify the pinned standalone MCP commit and vendored subtree."
    )
    parser.add_argument(
        "--repo",
        type=Path,
        default=Path.cwd(),
        help="path inside the control-plane repository (default: current directory)",
    )
    parser.add_argument(
        "--source-repo",
        type=Path,
        help=(
            "optional path to a standalone MCP clone containing the pinned commit; "
            "no fetch or network access is performed"
        ),
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    try:
        evidence = verify_snapshot(
            arguments.repo,
            source_repository=arguments.source_repo,
        )
    except SnapshotVerificationError as error:
        print(f"MCP snapshot verification failed: {error}", file=sys.stderr)
        return 1

    source_status = "checked" if evidence.source_checked else "not-requested"
    print(
        "MCP snapshot verified "
        f"(revision={evidence.revision}, tree={evidence.tree}, source={source_status})"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

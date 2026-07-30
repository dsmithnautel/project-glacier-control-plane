#!/usr/bin/env python3
"""Validate that the reusable Project Glacier repository boundary is safe.

The validator is intentionally read-only.  It asks Git for the exact set of
tracked and non-ignored untracked files that could be committed, and inspects
only that set.  Ignored live state is never opened.
"""

from __future__ import annotations

import argparse
import errno
import os
import re
import stat
import subprocess
import sys
import unicodedata
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

EXPECTED_FILES = frozenset(
    {
        ".agents/skills/project-glacier-orchestrator/SKILL.md",
        ".agents/skills/project-glacier-orchestrator/agents/openai.yaml",
        ".agents/skills/project-glacier-orchestrator/references/task-tool-contract.md",
        ".gitignore",
        ".python-version",
        "AGENTS.md",
        "CONTRIBUTING.md",
        "MCP_SOURCE_REVISION",
        "MCP_SOURCE_TREE",
        "README.md",
        "SECURITY.md",
        "docs/ARCHITECTURE.md",
        "docs/OPERATIONS.md",
        "docs/PROVENANCE.md",
        "config/codex/agents/capacity-bottleneck-optimizer.toml.template",
        "config/codex/agents/mcp-interface-reliability.toml.template",
        "config/codex/config.toml.template",
        "mcp_server/.python-version",
        "mcp_server/CHANGELOG.md",
        "mcp_server/CONTRIBUTING.md",
        "mcp_server/README.md",
        "mcp_server/SECURITY.md",
        "mcp_server/.gitignore",
        "mcp_server/AGENTS.md",
        "mcp_server/config/codex.toml.example",
        "mcp_server/docs/PROVENANCE.md",
        "mcp_server/pyproject.toml",
        "mcp_server/src/project_glacier_mcp/__init__.py",
        "mcp_server/src/project_glacier_mcp/__main__.py",
        "mcp_server/src/project_glacier_mcp/broker.py",
        "mcp_server/src/project_glacier_mcp/credential.py",
        "mcp_server/src/project_glacier_mcp/errors.py",
        "mcp_server/src/project_glacier_mcp/journal.py",
        "mcp_server/src/project_glacier_mcp/models.py",
        "mcp_server/src/project_glacier_mcp/redaction.py",
        "mcp_server/src/project_glacier_mcp/server.py",
        "mcp_server/src/project_glacier_mcp/settings.py",
        "mcp_server/src/project_glacier_mcp/transport.py",
        "mcp_server/tests/__init__.py",
        "mcp_server/tests/conftest.py",
        "mcp_server/tests/fakes.py",
        "mcp_server/tests/test_broker.py",
        "mcp_server/tests/test_credential.py",
        "mcp_server/tests/test_journal.py",
        "mcp_server/tests/test_mcp_protocol.py",
        "mcp_server/tests/test_example_registration.py",
        "mcp_server/tests/test_settings.py",
        "mcp_server/tests/test_transport.py",
        "mcp_server/uv.lock",
        "project_glacier_claude_version.md",
        "project_glacier_master_orchestrator_prompt.md",
        "project_glacier_multi_root_orchestrator_prompt.md",
        "project_glacier_offline_root_worker_prompt.md",
        "project_glacier_orchestrator/__init__.py",
        "project_glacier_orchestrator/__main__.py",
        "project_glacier_orchestrator/canonical.py",
        "project_glacier_orchestrator/checkpoint.py",
        "project_glacier_orchestrator/cli.py",
        "project_glacier_orchestrator/codex_actions.py",
        "project_glacier_orchestrator/errors.py",
        "project_glacier_orchestrator/outbox.py",
        "project_glacier_orchestrator/protocol.py",
        "project_glacier_orchestrator/workflow.py",
        "pyproject.toml",
        "scripts/configure_codex.py",
        "scripts/initialize_workspace.py",
        "scripts/live_mcp_smoke.py",
        "scripts/validate_repository.py",
        "scripts/verify_mcp_snapshot.py",
        "templates/runtime/CURRENT_CHECKPOINT.md",
        "templates/runtime/glacier_artifact_index.md",
        "templates/runtime/glacier_coverage_matrix.md",
        "templates/runtime/glacier_experiment_ledger.md",
        "templates/runtime/glacier_findings_queue.md",
        "templates/runtime/glacier_hypothesis_registry.md",
        "templates/runtime/glacier_rules_dossier.md",
        "tests/__init__.py",
        "tests/support.py",
        "tests/test_canonical.py",
        "tests/test_checkpoint.py",
        "tests/test_configure_codex.py",
        "tests/test_initialize_workspace.py",
        "tests/test_mcp_registration.py",
        "tests/test_outbox.py",
        "tests/test_protocol.py",
        "tests/test_validate_repository.py",
        "tests/test_verify_mcp_snapshot.py",
        "tests/test_workflow.py",
    }
)

_ROOT_LIVE_FILES = frozenset(
    {
        "CURRENT_CHECKPOINT.md",
        "PROJECT_GLACIER_RESUME.txt",
    }
)

_ROOT_LIVE_ARTIFACTS = frozenset(
    {
        "glacier_artifact_index.md",
        "glacier_coverage_matrix.md",
        "glacier_experiment_ledger.md",
        "glacier_findings_queue.md",
        "glacier_hypothesis_registry.md",
        "glacier_rules_dossier.md",
    }
)

_FORBIDDEN_COMPONENTS = frozenset(
    {
        ".git",
        ".glacier-state",
        ".mypy_cache",
        ".nox",
        ".project-glacier-orchestrator",
        ".pytest_cache",
        ".ruff_cache",
        ".tox",
        ".venv",
        "__pycache__",
        "build",
        "dist",
        "htmlcov",
    }
)

_CONTENT_RULES = (
    (b"/Users/" + b"dereksmithnautel", "machine-specific-path"),
    (b"ocid1." + b"tenancy.", "tenancy-identifier"),
)

_GIT_OBJECT_RE = re.compile(rb"[0-9a-f]{40}(?:\r?\n)?")
MAX_CANDIDATE_BYTES = 16 * 1024 * 1024


class ValidationError(RuntimeError):
    """Raised when the repository cannot be inspected safely."""


@dataclass(frozen=True, order=True)
class Issue:
    """One redacted repository-boundary violation."""

    path: str
    rule: str


@dataclass(frozen=True)
class IndexEntry:
    """One path and object identity from Git's index."""

    mode: str
    object_id: str
    stage: str


def _run_git(start: Path, arguments: Sequence[str]) -> bytes:
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
            os.fspath(start),
            *arguments,
        ],
        check=False,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        env=environment,
    )
    if result.returncode != 0:
        operation = arguments[0] if arguments else "repository"
        raise ValidationError(f"Git {operation} inspection failed")
    return result.stdout


def discover_repository(start: Path) -> Path:
    """Return the canonical Git worktree root containing *start*."""

    try:
        canonical_start = start.expanduser().resolve(strict=True)
    except OSError as error:
        raise ValidationError("the requested repository path is unavailable") from error
    if not canonical_start.is_dir():
        raise ValidationError("the requested repository path is not a directory")
    if any(ord(character) < 32 or ord(character) == 127 for character in os.fspath(canonical_start)):
        raise ValidationError("repository paths may not contain control characters")

    raw_root = _run_git(canonical_start, ("rev-parse", "--show-toplevel"))
    decoded_root = os.fsdecode(raw_root.rstrip(b"\r\n"))
    if not decoded_root:
        raise ValidationError("Git returned an empty worktree root")
    try:
        root = Path(decoded_root).resolve(strict=True)
    except OSError as error:
        raise ValidationError("Git returned an unavailable worktree root") from error
    if not root.is_dir():
        raise ValidationError("Git worktree root is not a directory")
    return root


def candidate_paths(root: Path) -> tuple[str, ...]:
    """Return Git's tracked plus non-ignored untracked candidate file set."""

    raw_paths = _run_git(
        root,
        ("ls-files", "--cached", "--others", "--exclude-standard", "-z"),
    )
    decoded = tuple(os.fsdecode(item) for item in raw_paths.split(b"\0") if item)
    return tuple(sorted(set(decoded)))


def _index_entries(root: Path) -> dict[str, IndexEntry]:
    """Return stage-aware index entries without consulting working-tree bytes."""

    raw_entries = _run_git(root, ("ls-files", "--stage", "-z"))
    entries: dict[str, IndexEntry] = {}
    for raw_entry in (item for item in raw_entries.split(b"\0") if item):
        try:
            raw_header, raw_path = raw_entry.split(b"\t", 1)
            raw_mode, raw_object_id, raw_stage = raw_header.split()
        except ValueError as exc:
            raise ValidationError("Git returned a malformed index entry") from exc
        path = os.fsdecode(raw_path)
        entry = IndexEntry(
            mode=raw_mode.decode("ascii"),
            object_id=raw_object_id.decode("ascii"),
            stage=raw_stage.decode("ascii"),
        )
        if path in entries:
            entry = IndexEntry(
                mode=entry.mode,
                object_id=entry.object_id,
                stage="unmerged",
            )
        entries[path] = entry
    return entries


def _path_issue(path: str) -> Issue | None:
    pure_path = PurePosixPath(path)
    normalized_path = unicodedata.normalize("NFKC", path).casefold()
    normalized_parts = PurePosixPath(normalized_path).parts
    if (
        not path
        or pure_path.is_absolute()
        or any(component in {"", ".", ".."} for component in pure_path.parts)
        or any(ord(character) < 32 or ord(character) == 127 for character in path)
    ):
        return Issue(path or "<empty>", "unsafe-repository-path")

    if normalized_path in {value.casefold() for value in _ROOT_LIVE_FILES}:
        return Issue(path, "live-runtime-file")
    if normalized_path in {value.casefold() for value in _ROOT_LIVE_ARTIFACTS}:
        return Issue(path, "live-populated-artifact")
    if (
        normalized_path.startswith(".project_glacier_resume.")
        or normalized_path.startswith(".project-glacier-init-")
        or normalized_path == ".project-glacier-orchestrator.lock"
    ):
        return Issue(path, "runtime-or-cache-path")
    if any(
        component == ".env"
        or (component.startswith(".env.") and component != ".env.example")
        for component in normalized_parts
    ):
        return Issue(path, "runtime-or-cache-path")
    if normalized_path.endswith(".log") or any(
        component in {".coverage", "coverage.xml"} for component in normalized_parts
    ):
        return Issue(path, "runtime-or-cache-path")
    if (
        normalized_path in {".ds_store", "tmp"}
        or normalized_path.startswith("tmp/")
        or normalized_path.endswith((".pyc", ".pyo"))
        or any(component.endswith(".egg-info") for component in normalized_parts)
    ):
        return Issue(path, "runtime-or-cache-path")

    forbidden = next(
        (
            component
            for component in normalized_parts
            if component in _FORBIDDEN_COMPONENTS
        ),
        None,
    )
    if forbidden is not None:
        rule = "nested-git-metadata" if forbidden == ".git" else "runtime-or-cache-path"
        return Issue(path, rule)

    if normalized_path == ".codex/config.toml":
        return Issue(path, "live-codex-configuration")
    if (
        normalized_path.startswith(".codex/")
        and ".configure-codex-" in PurePosixPath(normalized_path).name
        and normalized_path.endswith(".tmp")
    ):
        return Issue(path, "runtime-or-cache-path")
    if (
        len(normalized_parts) == 3
        and normalized_parts[:2] == (".codex", "agents")
        and normalized_parts[2].endswith(".toml")
    ):
        return Issue(path, "live-codex-agent-configuration")
    return None


def _content_issues(path: str, content: bytes) -> list[Issue]:
    matches: set[str] = set()
    for pattern, rule in _CONTENT_RULES:
        if pattern in content:
            matches.add(rule)
    return [Issue(path, rule) for rule in sorted(matches)]


def _open_repository_root(root: Path) -> int:
    """Pin the repository root for descriptor-relative untracked reads."""

    required = ("O_DIRECTORY", "O_NOFOLLOW")
    if any(not hasattr(os, name) for name in required):
        raise ValidationError("safe filesystem flags are unavailable")
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW  # type: ignore[attr-defined]
    flags |= getattr(os, "O_CLOEXEC", 0)
    try:
        descriptor = os.open(root, flags)
    except OSError as exc:
        raise ValidationError("repository root could not be opened safely") from exc
    metadata = os.fstat(descriptor)
    if not stat.S_ISDIR(metadata.st_mode):
        os.close(descriptor)
        raise ValidationError("repository root is not a directory")
    return descriptor


def _read_worktree_candidate(
    root_fd: int,
    path: str,
) -> tuple[bytes | None, Issue | None]:
    """Read an untracked file through a no-follow descriptor chain."""

    if not hasattr(os, "O_NOFOLLOW") or not hasattr(os, "O_DIRECTORY"):
        return None, Issue(path, "safe-filesystem-flags-unavailable")
    parts = PurePosixPath(path).parts
    current_fd = os.dup(root_fd)
    try:
        for component in parts[:-1]:
            flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW  # type: ignore[attr-defined]
            flags |= os.O_NONBLOCK | getattr(os, "O_CLOEXEC", 0)
            try:
                next_fd = os.open(component, flags, dir_fd=current_fd)
            except OSError as exc:
                rule = (
                    "symlink-not-inspected"
                    if exc.errno == errno.ELOOP
                    else "unsafe-repository-path"
                    if exc.errno == errno.ENOTDIR
                    else "unreadable-candidate"
                )
                return None, Issue(path, rule)
            os.close(current_fd)
            current_fd = next_fd

        flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK  # type: ignore[attr-defined]
        flags |= getattr(os, "O_CLOEXEC", 0)
        try:
            descriptor = os.open(parts[-1], flags, dir_fd=current_fd)
        except OSError as exc:
            rule = "symlink-not-inspected" if exc.errno == errno.ELOOP else "unreadable-candidate"
            return None, Issue(path, rule)
    finally:
        os.close(current_fd)

    try:
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode):
            return None, Issue(path, "non-regular-file")
        chunks: list[bytes] = []
        remaining = MAX_CANDIDATE_BYTES + 1
        while remaining > 0:
            chunk = os.read(descriptor, min(64 * 1024, remaining))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        content = b"".join(chunks)
        if len(content) > MAX_CANDIDATE_BYTES:
            return None, Issue(path, "candidate-too-large")
        return content, None
    finally:
        os.close(descriptor)


def _read_index_candidate(
    root: Path,
    path: str,
    entry: IndexEntry,
) -> tuple[bytes | None, Issue | None]:
    """Read the exact bytes Git would commit, not the working-tree replacement."""

    if entry.stage != "0":
        return None, Issue(path, "unmerged-index-entry")
    if entry.mode == "120000":
        return None, Issue(path, "symlink-not-inspected")
    if entry.mode == "160000":
        return None, Issue(path, "nested-git-metadata")
    if entry.mode not in {"100644", "100755"}:
        return None, Issue(path, "non-regular-index-entry")
    raw_size = _run_git(root, ("cat-file", "-s", entry.object_id))
    try:
        object_size = int(raw_size.strip())
    except ValueError as exc:
        raise ValidationError("Git returned an invalid blob size") from exc
    if object_size < 0:
        raise ValidationError("Git returned an invalid blob size")
    if object_size > MAX_CANDIDATE_BYTES:
        return None, Issue(path, "candidate-too-large")
    content = _run_git(root, ("cat-file", "blob", entry.object_id))
    if len(content) != object_size:
        raise ValidationError("Git blob size changed during repository validation")
    return content, None


def validate_repository(start: Path) -> tuple[Path, tuple[str, ...], tuple[Issue, ...]]:
    """Inspect a repository and return its root, candidates, and violations."""

    root = discover_repository(start)
    index_entries = _index_entries(root)
    candidates = candidate_paths(root)
    candidate_set = set(candidates)
    required_candidate_set = set(index_entries) if index_entries else candidate_set
    issues: set[Issue] = {
        Issue(path, "missing-reusable-file")
        for path in EXPECTED_FILES - required_candidate_set
    }

    pin_rules = {
        "MCP_SOURCE_REVISION": "invalid-source-revision",
        "MCP_SOURCE_TREE": "invalid-source-tree",
    }
    pin_contents: dict[str, bytes] = {}
    root_fd = _open_repository_root(root)
    try:
        for path in candidates:
            path_issue = _path_issue(path)
            if path_issue is not None:
                issues.add(path_issue)
                continue
            index_entry = index_entries.get(path)
            if index_entry is not None:
                content, content_issue = _read_index_candidate(root, path, index_entry)
            else:
                content, content_issue = _read_worktree_candidate(root_fd, path)
            if content_issue is not None:
                issues.add(content_issue)
                continue
            assert content is not None
            if path in pin_rules:
                pin_contents[path] = content
            issues.update(_content_issues(path, content))
    finally:
        os.close(root_fd)

    for pin_path, rule in pin_rules.items():
        pin_content = pin_contents.get(pin_path)
        if pin_content is not None and _GIT_OBJECT_RE.fullmatch(pin_content) is None:
            issues.add(Issue(pin_path, rule))

    if _index_entries(root) != index_entries:
        raise ValidationError("Git index changed during repository validation")

    return root, candidates, tuple(sorted(issues))


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Validate Project Glacier's reusable repository boundary."
    )
    parser.add_argument(
        "--repo",
        type=Path,
        default=Path.cwd(),
        help="path inside the repository (default: current directory)",
    )
    return parser


def _diagnostic_path(path: str) -> str:
    """Render a candidate path without control injection or sensitive aliases."""

    lowered = path.casefold()
    if (
        "/users/dereksmithnautel" in lowered
        or "ocid1." + "tenancy." in lowered
    ):
        return "<redacted-path>"
    return path.encode("unicode_escape", errors="backslashreplace").decode("ascii")


def main(argv: Sequence[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    try:
        _root, candidates, issues = validate_repository(arguments.repo)
    except ValidationError as error:
        print(f"repository validation could not run: {error}", file=sys.stderr)
        return 2

    if issues:
        print(f"repository validation failed ({len(issues)} issue(s))")
        for issue in issues:
            print(f"- {_diagnostic_path(issue.path)}: {issue.rule}")
        return 1

    print(f"repository validation passed ({len(candidates)} candidate files)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Render Project Glacier's repository-local Codex configuration.

The templates contain no participant credential. This program never consults
the environment or macOS Keychain for one, and it never prints existing
configuration contents.
"""

from __future__ import annotations

import argparse
import errno
import os
import re
import secrets
import stat
import subprocess
import sys
import tomllib
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Final


class ConfigurationError(RuntimeError):
    """A safe, user-actionable configuration failure."""


@dataclass(frozen=True)
class RenderSpec:
    """One template and its repository-relative destination."""

    template: Path
    destination: Path


RENDER_SPECS: Final[tuple[RenderSpec, ...]] = (
    RenderSpec(
        Path("config/codex/config.toml.template"),
        Path(".codex/config.toml"),
    ),
    RenderSpec(
        Path("config/codex/agents/capacity-bottleneck-optimizer.toml.template"),
        Path(".codex/agents/capacity-bottleneck-optimizer.toml"),
    ),
    RenderSpec(
        Path("config/codex/agents/mcp-interface-reliability.toml.template"),
        Path(".codex/agents/mcp-interface-reliability.toml"),
    ),
)

TOKEN_PATTERN: Final[re.Pattern[str]] = re.compile(r"\{\{[A-Z][A-Z0-9_]*\}\}")
FILE_MODE: Final[int] = 0o600
DIRECTORY_MODE: Final[int] = 0o700
IMPORT_PROBE_MARKER: Final[bytes] = b"PROJECT_GLACIER_MCP_IMPORT_OK\n"
IMPORT_PROBE_TIMEOUT_SECONDS: Final[int] = 15
MAX_TEMPLATE_BYTES: Final[int] = 1024 * 1024


def _absolute_without_resolving(path: Path) -> Path:
    """Return an absolute path without hiding a symlink at the final component."""

    return Path(os.path.abspath(os.fspath(path.expanduser())))


def _looks_like_repo_root(path: Path) -> bool:
    """Return whether all configuration templates exist below ``path``."""

    return all((path / spec.template).is_file() for spec in RENDER_SPECS)


def discover_repo_root(explicit: Path | None = None) -> Path:
    """Find and validate the repository root.

    An explicit root wins. Otherwise, search upward from both the current
    directory and this script. The path is intentionally not resolved here;
    ``_open_root`` rejects a symlink used as the repository root.
    """

    if explicit is not None:
        candidate = _absolute_without_resolving(explicit)
        if not _looks_like_repo_root(candidate):
            raise ConfigurationError(
                f"not a Project Glacier repository root (templates missing): {candidate}"
            )
        return candidate

    starts = (
        _absolute_without_resolving(Path.cwd()),
        _absolute_without_resolving(Path(__file__)).parent,
    )
    seen: set[Path] = set()
    for start in starts:
        for candidate in (start, *start.parents):
            if candidate in seen:
                continue
            seen.add(candidate)
            if _looks_like_repo_root(candidate):
                return candidate

    raise ConfigurationError(
        "could not discover the Project Glacier repository root; use --repo-root"
    )


def _toml_string_contents(value: Path) -> str:
    """Escape a path for insertion inside a TOML basic string."""

    escaped: list[str] = []
    named_escapes = {
        "\b": r"\b",
        "\t": r"\t",
        "\n": r"\n",
        "\f": r"\f",
        "\r": r"\r",
        '"': r"\"",
        "\\": r"\\",
    }
    for character in os.fspath(value):
        if character in named_escapes:
            escaped.append(named_escapes[character])
            continue
        codepoint = ord(character)
        if 0xD800 <= codepoint <= 0xDFFF:
            raise ConfigurationError(
                "repository paths must contain valid Unicode scalar values"
            )
        if not character.isprintable():
            width = 4 if codepoint <= 0xFFFF else 8
            prefix = "u" if width == 4 else "U"
            escaped.append(f"\\{prefix}{codepoint:0{width}X}")
            continue
        escaped.append(character)
    return "".join(escaped)


def _render_template(
    template_path: Path,
    template: str,
    replacements: dict[str, Path],
) -> bytes:
    """Render one UTF-8 template and reject unresolved placeholder tokens."""

    unresolved = sorted(set(TOKEN_PATTERN.findall(template)) - replacements.keys())
    if unresolved:
        raise ConfigurationError(
            f"unresolved token(s) in {template_path}: {', '.join(unresolved)}"
        )

    rendered = TOKEN_PATTERN.sub(
        lambda match: _toml_string_contents(replacements[match.group(0)]),
        template,
    )
    try:
        tomllib.loads(rendered)
    except tomllib.TOMLDecodeError as exc:
        raise ConfigurationError(f"rendered template is invalid TOML: {template_path}") from exc
    return rendered.encode("utf-8")


def _validate_mcp_environment(mcp_bin_fd: int, mcp_python: Path) -> None:
    """Verify that the configured executable can import the broker entrypoint."""

    try:
        executable_metadata = os.stat(
            "python",
            dir_fd=mcp_bin_fd,
            follow_symlinks=True,
        )
    except FileNotFoundError:
        raise ConfigurationError(
            f"MCP Python does not exist; create the locked MCP environment first: {mcp_python}"
        ) from None
    except OSError as exc:
        raise ConfigurationError(f"MCP Python cannot be inspected safely: {mcp_python}") from exc
    if not stat.S_ISREG(executable_metadata.st_mode):
        raise ConfigurationError(f"MCP Python is not a regular file: {mcp_python}")
    if stat.S_IMODE(executable_metadata.st_mode) & 0o111 == 0:
        raise ConfigurationError(f"MCP Python is not executable: {mcp_python}")

    probe = (
        "import sys; import project_glacier_mcp.server; "
        f"sys.stdout.buffer.write({IMPORT_PROBE_MARKER!r})"
    )
    try:
        result = subprocess.run(
            ["./python", "-I", "-c", probe],
            check=False,
            preexec_fn=lambda: os.fchdir(mcp_bin_fd),
            pass_fds=(mcp_bin_fd,),
            stdin=subprocess.DEVNULL,
            capture_output=True,
            timeout=IMPORT_PROBE_TIMEOUT_SECONDS,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise ConfigurationError(
            "MCP Python could not execute the broker import probe."
        ) from exc
    if result.returncode != 0 or result.stdout != IMPORT_PROBE_MARKER:
        raise ConfigurationError(
            "MCP Python cannot import project_glacier_mcp.server from the locked environment."
        )


def _directory_open_flags() -> int:
    required = ("O_DIRECTORY", "O_NOFOLLOW")
    missing = [name for name in required if not hasattr(os, name)]
    if missing:
        raise ConfigurationError(
            "this platform lacks required safe filesystem flags: " + ", ".join(missing)
        )
    return os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW  # type: ignore[attr-defined]


def _open_root(repo_root: Path) -> int:
    """Open the repository root without following a final symlink."""

    try:
        root_fd = os.open(repo_root, _directory_open_flags())
    except OSError as exc:
        if exc.errno in (errno.ELOOP, errno.ENOTDIR):
            raise ConfigurationError(
                f"repository root must be a real directory, not a symlink: {repo_root}"
            ) from exc
        raise ConfigurationError(f"cannot open repository root {repo_root}: {exc}") from exc

    root_stat = os.fstat(root_fd)
    if not stat.S_ISDIR(root_stat.st_mode):
        os.close(root_fd)
        raise ConfigurationError(f"repository root is not a directory: {repo_root}")
    if root_stat.st_uid != os.geteuid():
        os.close(root_fd)
        raise ConfigurationError("repository root must be owned by the current user")
    if stat.S_IMODE(root_stat.st_mode) & 0o022:
        os.close(root_fd)
        raise ConfigurationError(
            "repository root must not be writable by group or other users"
        )
    return root_fd


def _verify_root_path(repo_root: Path, root_fd: int) -> None:
    """Require the nominal repository path to still name the pinned root."""

    try:
        path_metadata = repo_root.stat(follow_symlinks=False)
        descriptor_metadata = os.fstat(root_fd)
    except OSError as exc:
        raise ConfigurationError("repository root identity changed during configuration") from exc
    if (
        not stat.S_ISDIR(path_metadata.st_mode)
        or path_metadata.st_dev != descriptor_metadata.st_dev
        or path_metadata.st_ino != descriptor_metadata.st_ino
    ):
        raise ConfigurationError("repository root identity changed during configuration")


def _validate_directory_descriptor(directory_fd: int, label: Path) -> None:
    """Require a trusted directory before using it for code or configuration."""

    metadata = os.fstat(directory_fd)
    if not stat.S_ISDIR(metadata.st_mode):
        raise ConfigurationError(f"destination parent is not a directory: {label}")
    if metadata.st_uid != os.geteuid():
        raise ConfigurationError(
            f"destination parent must be owned by the current user: {label}"
        )
    if stat.S_IMODE(metadata.st_mode) & 0o022:
        raise ConfigurationError(
            f"destination parent must not be writable by group or other users: {label}"
        )


def _verify_relative_directory(
    root_fd: int,
    relative_path: Path,
    expected_fd: int,
) -> None:
    """Require a relative directory path to still name its pinned descriptor."""

    current_fd = _open_parent_directory(
        root_fd,
        relative_path,
        create=False,
    )
    if current_fd is None:
        raise ConfigurationError(f"required directory disappeared: {relative_path}")
    try:
        current_metadata = os.fstat(current_fd)
        expected_metadata = os.fstat(expected_fd)
        if (
            current_metadata.st_dev != expected_metadata.st_dev
            or current_metadata.st_ino != expected_metadata.st_ino
        ):
            raise ConfigurationError(
                f"required directory identity changed: {relative_path}"
            )
    finally:
        os.close(current_fd)


def _open_parent_directory(
    root_fd: int,
    relative_parent: Path,
    *,
    create: bool,
    missing_ok: bool = False,
) -> int | None:
    """Open a destination parent one component at a time without symlinks."""

    if relative_parent.is_absolute() or ".." in relative_parent.parts:
        raise ConfigurationError(f"unsafe destination parent: {relative_parent}")

    current_fd = os.dup(root_fd)
    try:
        for component in relative_parent.parts:
            if component in ("", "."):
                continue
            try:
                next_fd = os.open(
                    component,
                    _directory_open_flags(),
                    dir_fd=current_fd,
                )
            except FileNotFoundError:
                if not create:
                    if missing_ok:
                        os.close(current_fd)
                        return None
                    raise ConfigurationError(
                        f"destination parent does not exist: {relative_parent}"
                    ) from None
                try:
                    os.mkdir(component, DIRECTORY_MODE, dir_fd=current_fd)
                except FileExistsError:
                    # Another process created it; the no-follow open below
                    # verifies what appeared.
                    pass
                except OSError as exc:
                    raise ConfigurationError(
                        f"cannot create destination directory {relative_parent}: {exc}"
                    ) from exc
                try:
                    next_fd = os.open(
                        component,
                        _directory_open_flags(),
                        dir_fd=current_fd,
                    )
                except OSError as exc:
                    raise ConfigurationError(
                        f"new destination parent is unsafe: {relative_parent}"
                    ) from exc
            except OSError as exc:
                if exc.errno in (errno.ELOOP, errno.ENOTDIR):
                    raise ConfigurationError(
                        f"destination parent must not contain symlinks: {relative_parent}"
                    ) from exc
                raise ConfigurationError(
                    f"cannot open destination parent {relative_parent}: {exc}"
                ) from exc

            os.close(current_fd)
            current_fd = next_fd
            _validate_directory_descriptor(current_fd, relative_parent)
        return current_fd
    except BaseException:
        os.close(current_fd)
        raise


def _open_existing(parent_fd: int, name: str) -> tuple[int, os.stat_result] | None:
    """Open an existing regular destination without following a symlink."""

    flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK  # type: ignore[attr-defined]
    try:
        file_fd = os.open(name, flags, dir_fd=parent_fd)
    except FileNotFoundError:
        return None
    except OSError as exc:
        if exc.errno == errno.ELOOP:
            raise ConfigurationError(f"refusing symlink destination: {name}") from exc
        raise ConfigurationError(f"cannot safely open destination {name}: {exc}") from exc

    metadata = os.fstat(file_fd)
    if not stat.S_ISREG(metadata.st_mode):
        os.close(file_fd)
        raise ConfigurationError(f"destination is not a regular file: {name}")
    if metadata.st_nlink != 1:
        os.close(file_fd)
        raise ConfigurationError(f"destination must not have hard links: {name}")
    return file_fd, metadata


def _read_template(root_fd: int, relative_path: Path) -> str:
    """Read a small, single-linked template relative to the pinned root."""

    parent_fd = _open_parent_directory(
        root_fd,
        relative_path.parent,
        create=False,
    )
    if parent_fd is None:
        raise ConfigurationError(f"template parent is missing: {relative_path.parent}")
    try:
        opened = _open_existing(parent_fd, relative_path.name)
        if opened is None:
            raise ConfigurationError(f"template is missing: {relative_path}")
        file_fd, metadata = opened
        try:
            if metadata.st_size > MAX_TEMPLATE_BYTES:
                raise ConfigurationError(f"template is too large: {relative_path}")
            content = bytearray()
            while len(content) <= MAX_TEMPLATE_BYTES:
                chunk = os.read(file_fd, min(64 * 1024, MAX_TEMPLATE_BYTES + 1 - len(content)))
                if not chunk:
                    break
                content.extend(chunk)
            if len(content) > MAX_TEMPLATE_BYTES:
                raise ConfigurationError(f"template is too large: {relative_path}")
        finally:
            os.close(file_fd)
    finally:
        os.close(parent_fd)
    try:
        return bytes(content).decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ConfigurationError(f"template is not valid UTF-8: {relative_path}") from exc


def _file_matches(file_fd: int, expected: bytes) -> bool:
    """Compare a file without retaining or reporting its unknown contents."""

    offset = 0
    while offset < len(expected):
        chunk = os.read(file_fd, min(64 * 1024, len(expected) - offset))
        if not chunk or chunk != expected[offset : offset + len(chunk)]:
            return False
        offset += len(chunk)
    return os.read(file_fd, 1) == b""


def _atomic_write(parent_fd: int, name: str, contents: bytes) -> None:
    """Atomically replace ``name`` with a durable mode-0600 regular file."""

    temporary_name = f".{name}.configure-codex-{os.getpid()}-{secrets.token_hex(8)}.tmp"
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW  # type: ignore[attr-defined]
    temporary_fd: int | None = None
    try:
        temporary_fd = os.open(
            temporary_name,
            flags,
            FILE_MODE,
            dir_fd=parent_fd,
        )
        view = memoryview(contents)
        written = 0
        while written < len(view):
            count = os.write(temporary_fd, view[written:])
            if count <= 0:
                raise ConfigurationError(f"short write while creating {name}")
            written += count
        os.fchmod(temporary_fd, FILE_MODE)
        os.fsync(temporary_fd)
        os.close(temporary_fd)
        temporary_fd = None

        os.replace(
            temporary_name,
            name,
            src_dir_fd=parent_fd,
            dst_dir_fd=parent_fd,
        )
        os.fsync(parent_fd)
    except OSError as exc:
        raise ConfigurationError(f"cannot safely write destination {name}: {exc}") from exc
    finally:
        if temporary_fd is not None:
            os.close(temporary_fd)
        try:
            os.unlink(temporary_name, dir_fd=parent_fd)
        except FileNotFoundError:
            pass


def _process_destination(
    root_fd: int,
    spec: RenderSpec,
    expected: bytes,
    *,
    check: bool,
    force: bool,
) -> str:
    """Validate or render one destination and return an outcome label."""

    if spec.destination.is_absolute() or ".." in spec.destination.parts:
        raise ConfigurationError(f"unsafe destination: {spec.destination}")

    parent_fd = _open_parent_directory(
        root_fd,
        spec.destination.parent,
        create=not check,
    )
    if parent_fd is None:
        raise ConfigurationError(
            f"destination parent does not exist: {spec.destination.parent}"
        )
    try:
        existing = _open_existing(parent_fd, spec.destination.name)
        if existing is None:
            if check:
                raise ConfigurationError(
                    f"generated Codex configuration is missing: {spec.destination}"
                )
            _atomic_write(parent_fd, spec.destination.name, expected)
            return "created"

        file_fd, metadata = existing
        try:
            matches = _file_matches(file_fd, expected)
            mode = stat.S_IMODE(metadata.st_mode)
            if check:
                if not matches:
                    raise ConfigurationError(
                        f"generated Codex configuration differs: {spec.destination}"
                    )
                if mode != FILE_MODE:
                    raise ConfigurationError(
                        f"generated Codex configuration must have mode 0600: {spec.destination}"
                    )
                return "valid"

            if matches:
                if mode != FILE_MODE:
                    _atomic_write(parent_fd, spec.destination.name, expected)
                    return "permissions-updated"
                return "unchanged"
        finally:
            os.close(file_fd)

        if not force:
            raise ConfigurationError(
                f"refusing to overwrite differing file {spec.destination}; "
                "review it, then rerun with --force"
            )

        # Recheck the destination immediately before replacement. In
        # particular, never let --force replace a symlink introduced after the
        # first check.
        rechecked = _open_existing(parent_fd, spec.destination.name)
        if rechecked is None:
            raise ConfigurationError(
                f"destination disappeared while configuring: {spec.destination}"
            )
        rechecked_fd, _ = rechecked
        os.close(rechecked_fd)
        _atomic_write(parent_fd, spec.destination.name, expected)
        return "updated"
    finally:
        os.close(parent_fd)


def _preflight_destination(
    root_fd: int,
    spec: RenderSpec,
    expected: bytes,
    *,
    check: bool,
    force: bool,
) -> None:
    """Reject every predictable destination conflict before any file changes."""

    if spec.destination.is_absolute() or ".." in spec.destination.parts:
        raise ConfigurationError(f"unsafe destination: {spec.destination}")

    parent_fd = _open_parent_directory(
        root_fd,
        spec.destination.parent,
        create=False,
        missing_ok=True,
    )
    if parent_fd is None:
        if check:
            raise ConfigurationError(
                f"generated Codex configuration is missing: {spec.destination}"
            )
        return

    try:
        existing = _open_existing(parent_fd, spec.destination.name)
        if existing is None:
            if check:
                raise ConfigurationError(
                    f"generated Codex configuration is missing: {spec.destination}"
                )
            return

        file_fd, metadata = existing
        try:
            matches = _file_matches(file_fd, expected)
        finally:
            os.close(file_fd)

        if check:
            if not matches:
                raise ConfigurationError(
                    f"generated Codex configuration differs: {spec.destination}"
                )
            if stat.S_IMODE(metadata.st_mode) != FILE_MODE:
                raise ConfigurationError(
                    f"generated Codex configuration must have mode 0600: {spec.destination}"
                )
            return

        if not matches and not force:
            raise ConfigurationError(
                f"refusing to overwrite differing file {spec.destination}; "
                "review it, then rerun with --force"
            )
    finally:
        os.close(parent_fd)


def configure(
    repo_root: Path,
    *,
    check: bool = False,
    force: bool = False,
) -> list[tuple[Path, str]]:
    """Render or validate all repository-local Codex configuration files."""

    if check and force:
        raise ConfigurationError("--check and --force cannot be used together")

    repo_root = discover_repo_root(repo_root)
    mcp_root = repo_root / "mcp_server"
    mcp_python = mcp_root / ".venv/bin/python"
    state_dir = repo_root / ".glacier-state"

    root_fd = _open_root(repo_root)
    mcp_bin_fd: int | None = None
    outcomes: list[tuple[Path, str]] = []
    try:
        _verify_root_path(repo_root, root_fd)
        mcp_bin_fd = _open_parent_directory(
            root_fd,
            Path("mcp_server/.venv/bin"),
            create=False,
            missing_ok=True,
        )
        if mcp_bin_fd is None:
            raise ConfigurationError(
                f"MCP Python does not exist; create the locked MCP environment first: {mcp_python}"
            )

        _verify_relative_directory(
            root_fd,
            Path("mcp_server/.venv/bin"),
            mcp_bin_fd,
        )
        _validate_mcp_environment(mcp_bin_fd, mcp_python)
        _verify_root_path(repo_root, root_fd)
        _verify_relative_directory(
            root_fd,
            Path("mcp_server/.venv/bin"),
            mcp_bin_fd,
        )

        replacements = {
            "{{MCP_PYTHON}}": mcp_python,
            "{{MCP_ROOT}}": mcp_root,
            "{{STATE_DIR}}": state_dir,
        }
        rendered = [
            (
                spec,
                _render_template(
                    spec.template,
                    _read_template(root_fd, spec.template),
                    replacements,
                ),
            )
            for spec in RENDER_SPECS
        ]

        for spec, contents in rendered:
            _verify_root_path(repo_root, root_fd)
            _verify_relative_directory(
                root_fd,
                Path("mcp_server/.venv/bin"),
                mcp_bin_fd,
            )
            _preflight_destination(
                root_fd,
                spec,
                contents,
                check=check,
                force=force,
            )

        for spec, contents in rendered:
            _verify_root_path(repo_root, root_fd)
            _verify_relative_directory(
                root_fd,
                Path("mcp_server/.venv/bin"),
                mcp_bin_fd,
            )
            outcome = _process_destination(
                root_fd,
                spec,
                contents,
                check=check,
                force=force,
            )
            outcomes.append((spec.destination, outcome))
        _verify_root_path(repo_root, root_fd)
        _verify_relative_directory(
            root_fd,
            Path("mcp_server/.venv/bin"),
            mcp_bin_fd,
        )
    finally:
        if mcp_bin_fd is not None:
            os.close(mcp_bin_fd)
        os.close(root_fd)
    return outcomes


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=("Render or validate repository-local Project Glacier Codex configuration.")
    )
    parser.add_argument(
        "--repo-root",
        type=Path,
        help="Project Glacier repository root (auto-discovered when omitted)",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="validate generated files and the MCP import without writing",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="replace differing generated files after symlink-safety checks",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    try:
        repo_root = discover_repo_root(args.repo_root)
        outcomes = configure(
            repo_root,
            check=args.check,
            force=args.force,
        )
    except ConfigurationError as exc:
        print(f"configure_codex: {exc}", file=sys.stderr)
        return 2

    action = "validated" if args.check else "configured"
    print(f"Codex configuration {action} for {repo_root}")
    for path, outcome in outcomes:
        print(f"  {path}: {outcome}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

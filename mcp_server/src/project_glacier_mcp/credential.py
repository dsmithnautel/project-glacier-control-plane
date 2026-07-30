"""Interactive macOS Keychain setup for the participant credential."""

from __future__ import annotations

import subprocess
import sys

from .settings import DEFAULT_KEYCHAIN_SERVICE


def main() -> None:
    """Store the credential without placing it in shell history or repository files."""

    if sys.platform != "darwin":
        raise SystemExit("macOS Keychain setup is only available on macOS.")
    try:
        result = subprocess.run(
            [
                "/usr/bin/security",
                "add-generic-password",
                "-U",
                "-a",
                "project-glacier",
                "-s",
                DEFAULT_KEYCHAIN_SERVICE,
                "-w",
            ],
            check=False,
            stdout=subprocess.DEVNULL,
            timeout=60,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise SystemExit("Unable to invoke macOS Keychain securely.") from exc
    if result.returncode != 0:
        raise SystemExit("Unable to store the credential in macOS Keychain.")
    print(f"Credential stored in Keychain service: {DEFAULT_KEYCHAIN_SERVICE}")


if __name__ == "__main__":
    main()

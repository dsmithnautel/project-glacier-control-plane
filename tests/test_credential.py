from __future__ import annotations

from types import SimpleNamespace

import pytest

from project_glacier_mcp import credential


def test_keychain_helper_uses_native_prompt_without_secret_argument(monkeypatch):
    captured = {}

    def fake_run(arguments, **kwargs):
        captured["arguments"] = arguments
        captured["kwargs"] = kwargs
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(credential.sys, "platform", "darwin")
    monkeypatch.setattr(credential.subprocess, "run", fake_run)

    credential.main()

    assert captured["arguments"][-1] == "-w"
    assert captured["arguments"].count("-w") == 1
    assert "input" not in captured["kwargs"]
    assert "shell" not in captured["kwargs"]
    assert captured["kwargs"]["stdout"] is credential.subprocess.DEVNULL


def test_keychain_helper_reports_sanitized_failure(monkeypatch):
    def fake_run(_arguments, **_kwargs):
        raise TimeoutError("sensitive child detail")

    monkeypatch.setattr(credential.sys, "platform", "darwin")
    monkeypatch.setattr(credential.subprocess, "run", fake_run)

    with pytest.raises(SystemExit) as caught:
        credential.main()

    assert "sensitive child detail" not in str(caught.value)

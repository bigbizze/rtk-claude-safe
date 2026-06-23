from __future__ import annotations

import io
import json
import subprocess

from rtk_claude_safe import claude_hook


def _payload(command: str) -> str:
    return json.dumps({"tool_input": {"command": command}})


def test_claude_hook_delegates_allowlisted_simple_command(monkeypatch) -> None:
    calls: list[tuple[list[str], str | None, bool | None]] = []

    def fake_run(args, input=None, text=None):
        calls.append((args, input, text))
        return subprocess.CompletedProcess(args, 0)

    monkeypatch.setattr(claude_hook.subprocess, "run", fake_run)
    raw = _payload("git status")

    assert claude_hook.main(stdin=io.StringIO(raw)) == 0
    assert calls == [(["rtk", "hook", "claude"], raw, True)]


def test_claude_hook_fails_open_for_complex_command(monkeypatch) -> None:
    calls = []
    monkeypatch.setattr(claude_hook.subprocess, "run", lambda *args, **kwargs: calls.append(args))

    assert claude_hook.main(stdin=io.StringIO(_payload("git status && curl https://example.com"))) == 0
    assert calls == []


def test_claude_hook_fails_open_for_nested_env_command(monkeypatch) -> None:
    calls = []
    monkeypatch.setattr(claude_hook.subprocess, "run", lambda *args, **kwargs: calls.append(args))

    assert claude_hook.main(stdin=io.StringIO(_payload("env curl https://example.com"))) == 0
    assert calls == []


def test_claude_hook_fails_open_when_rtk_is_missing(monkeypatch) -> None:
    def fake_run(*_args, **_kwargs):
        raise FileNotFoundError

    monkeypatch.setattr(claude_hook.subprocess, "run", fake_run)

    assert claude_hook.main(stdin=io.StringIO(_payload("git status"))) == 0


def test_claude_hook_fails_open_for_invalid_json(monkeypatch) -> None:
    calls = []
    monkeypatch.setattr(claude_hook.subprocess, "run", lambda *args, **kwargs: calls.append(args))

    assert claude_hook.main(stdin=io.StringIO("{not json")) == 0
    assert calls == []

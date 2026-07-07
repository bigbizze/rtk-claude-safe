from __future__ import annotations

import io
import json

import pytest

from rtk_claude_safe import codex_hook


@pytest.fixture(autouse=True)
def _rtk_available(monkeypatch) -> None:
    monkeypatch.setattr(codex_hook, "runtime_rtk_supported", lambda: True)


def _run_hook(payload: str | dict) -> tuple[int, str]:
    stdin = io.StringIO(payload if isinstance(payload, str) else json.dumps(payload))
    stdout = io.StringIO()
    rc = codex_hook.main(stdin=stdin, stdout=stdout)
    return rc, stdout.getvalue()


def test_codex_hook_rewrites_allowlisted_bash_command() -> None:
    rc, output = _run_hook({"tool_name": "Bash", "tool_input": {"command": "git status"}})

    assert rc == 0
    assert json.loads(output) == {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "allow",
            "updatedInput": {"command": "rtk git status"},
        }
    }


def test_codex_hook_uses_mapped_rewrite_command() -> None:
    rc, output = _run_hook(
        {"tool_name": "Bash", "tool_input": {"command": "eslint .", "description": "lint"}}
    )

    assert rc == 0
    assert json.loads(output)["hookSpecificOutput"]["updatedInput"] == {
        "command": "rtk lint .",
        "description": "lint",
    }


def test_codex_hook_rewrites_allowlisted_segments_in_shell_list() -> None:
    rc, output = _run_hook(
        {"tool_name": "Bash", "tool_input": {"command": "cd app && npm run test && git status"}}
    )

    assert rc == 0
    assert json.loads(output)["hookSpecificOutput"]["updatedInput"] == {
        "command": "cd app && rtk npm run test && rtk git status",
    }


def test_codex_hook_allows_gofmt_policy_exception_before_go_test() -> None:
    rc, output = _run_hook(
        {
            "tool_name": "Bash",
            "tool_input": {"command": "gofmt -w main.go git.go && go test ./..."},
        }
    )

    assert rc == 0
    assert json.loads(output) == {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "allow",
            "updatedInput": {"command": "gofmt -w main.go git.go && rtk go test ./..."},
        }
    }


def test_codex_hook_emits_nothing_for_non_allowlisted_command() -> None:
    rc, output = _run_hook({"tool_name": "Bash", "tool_input": {"command": "ls"}})

    assert rc == 0
    assert output == ""


def test_codex_hook_does_not_probe_rtk_for_fail_open_inputs(monkeypatch) -> None:
    def fail() -> bool:
        raise AssertionError("runtime probe should not be called")

    monkeypatch.setattr(codex_hook, "runtime_rtk_supported", fail)

    for payload in [
        "{not json",
        {"tool_name": "apply_patch", "tool_input": {"command": "git status"}},
        {"tool_name": "Bash", "tool_input": {"command": "ls"}},
        {"tool_name": "Bash", "tool_input": {"command": "git status | cat"}},
        {"tool_name": "Bash", "tool_input": {"command": "rtk git status"}},
    ]:
        rc, output = _run_hook(payload)
        assert rc == 0
        assert output == ""


def test_codex_hook_emits_nothing_for_non_bash_tool() -> None:
    rc, output = _run_hook({"tool_name": "apply_patch", "tool_input": {"command": "git status"}})

    assert rc == 0
    assert output == ""


def test_codex_hook_emits_nothing_for_invalid_json() -> None:
    rc, output = _run_hook("{not json")

    assert rc == 0
    assert output == ""


def test_codex_hook_emits_nothing_for_unsafe_shell_command() -> None:
    rc, output = _run_hook(
        {"tool_name": "Bash", "tool_input": {"command": "git status | cat"}}
    )

    assert rc == 0
    assert output == ""


def test_codex_hook_emits_nothing_for_risky_subset() -> None:
    rc, output = _run_hook({"tool_name": "Bash", "tool_input": {"command": "npm run dev"}})

    assert rc == 0
    assert output == ""


def test_codex_hook_emits_nothing_for_already_wrapped_command() -> None:
    rc, output = _run_hook({"tool_name": "Bash", "tool_input": {"command": "rtk git status"}})

    assert rc == 0
    assert output == ""


def test_codex_hook_emits_nothing_when_rtk_is_missing(monkeypatch) -> None:
    monkeypatch.setattr(codex_hook, "runtime_rtk_supported", lambda: False)

    rc, output = _run_hook({"tool_name": "Bash", "tool_input": {"command": "git status"}})

    assert rc == 0
    assert output == ""

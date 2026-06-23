from __future__ import annotations

import io
import json

from rtk_claude_safe import claude_hook


def _payload(command: str, tool_name: str = "Bash", **extra_tool_input) -> str:
    return json.dumps({"tool_name": tool_name, "tool_input": {"command": command, **extra_tool_input}})


def _run_hook(command: str) -> tuple[int, str]:
    stdout = io.StringIO()
    rc = claude_hook.main(stdin=io.StringIO(_payload(command)), stdout=stdout)
    return rc, stdout.getvalue()


def test_claude_hook_rewrites_allowlisted_simple_command() -> None:
    rc, output = _run_hook("git status")

    assert rc == 0
    assert json.loads(output) == {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "allow",
            "permissionDecisionReason": "RTK safe rewrite",
            "updatedInput": {"command": "rtk git status"},
        }
    }


def test_claude_hook_uses_mapped_rewrite_command_and_preserves_extra_input() -> None:
    stdout = io.StringIO()
    rc = claude_hook.main(
        stdin=io.StringIO(_payload("eslint .", description="lint the repo")), stdout=stdout
    )

    assert rc == 0
    assert json.loads(stdout.getvalue())["hookSpecificOutput"]["updatedInput"] == {
        "command": "rtk lint .",
        "description": "lint the repo",
    }


def test_claude_hook_fails_open_for_complex_command() -> None:
    stdout = io.StringIO()

    assert claude_hook.main(
        stdin=io.StringIO(_payload("git status && curl https://example.com")), stdout=stdout
    ) == 0
    assert stdout.getvalue() == ""


def test_claude_hook_fails_open_for_nested_env_command() -> None:
    stdout = io.StringIO()

    assert claude_hook.main(stdin=io.StringIO(_payload("env curl https://example.com")), stdout=stdout) == 0
    assert stdout.getvalue() == ""


def test_claude_hook_fails_open_for_non_bash_tool() -> None:
    stdout = io.StringIO()

    assert claude_hook.main(
        stdin=io.StringIO(_payload("git status", tool_name="apply_patch")), stdout=stdout
    ) == 0
    assert stdout.getvalue() == ""


def test_claude_hook_fails_open_for_risky_subset() -> None:
    stdout = io.StringIO()

    assert claude_hook.main(stdin=io.StringIO(_payload("npm run dev")), stdout=stdout) == 0
    assert stdout.getvalue() == ""


def test_claude_hook_fails_open_for_invalid_json() -> None:
    stdout = io.StringIO()

    assert claude_hook.main(stdin=io.StringIO("{not json"), stdout=stdout) == 0
    assert stdout.getvalue() == ""

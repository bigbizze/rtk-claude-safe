from __future__ import annotations

import io
import json

from rtk_claude_safe.codex_hook import main


def _run_hook(payload: str | dict) -> tuple[int, str]:
    stdin = io.StringIO(payload if isinstance(payload, str) else json.dumps(payload))
    stdout = io.StringIO()
    rc = main(stdin=stdin, stdout=stdout)
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
    rc, output = _run_hook({"tool_name": "Bash", "tool_input": {"command": "eslint ."}})

    assert rc == 0
    assert json.loads(output)["hookSpecificOutput"]["updatedInput"] == {"command": "rtk lint ."}


def test_codex_hook_emits_nothing_for_non_allowlisted_command() -> None:
    rc, output = _run_hook({"tool_name": "Bash", "tool_input": {"command": "ls"}})

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


def test_codex_hook_emits_nothing_for_complex_command() -> None:
    rc, output = _run_hook(
        {"tool_name": "Bash", "tool_input": {"command": "git status && git diff --stat"}}
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

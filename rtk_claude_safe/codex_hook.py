"""Codex PreToolUse hook handler for safe rtk rewrites."""

from __future__ import annotations

import json
import shutil
import sys
from typing import Any, TextIO

from rtk_claude_safe.allowlist import rewrite_command_for_agent


def build_rewrite_output(tool_input: dict[str, Any], command: str) -> dict[str, Any]:
    """Build Codex's supported PreToolUse rewrite payload."""
    updated_input = dict(tool_input)
    updated_input["command"] = command
    return {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "allow",
            "updatedInput": updated_input,
        }
    }


def maybe_rewrite_payload(payload: Any) -> dict[str, Any] | None:
    """Return a Codex rewrite payload, or None to fail open."""
    if not isinstance(payload, dict):
        return None
    if payload.get("tool_name") != "Bash":
        return None

    tool_input = payload.get("tool_input")
    if not isinstance(tool_input, dict):
        return None

    command = tool_input.get("command")
    if not isinstance(command, str):
        return None

    rewrite = rewrite_command_for_agent(command, "codex")
    if rewrite is None:
        return None
    if shutil.which("rtk") is None:
        return None

    return build_rewrite_output(tool_input, rewrite)


def main(stdin: TextIO | None = None, stdout: TextIO | None = None) -> int:
    """Run the hook from stdin/stdout. Invalid input is intentionally ignored."""
    stdin = stdin or sys.stdin
    stdout = stdout or sys.stdout

    try:
        payload = json.loads(stdin.read())
    except json.JSONDecodeError:
        return 0

    rewrite = maybe_rewrite_payload(payload)
    if rewrite is None:
        return 0

    json.dump(rewrite, stdout, separators=(",", ":"))
    stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

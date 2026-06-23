"""Codex PreToolUse hook handler for safe rtk rewrites."""

from __future__ import annotations

import json
import sys
from typing import Any, TextIO

from rtk_claude_safe.allowlist import should_wrap_command


def build_rewrite_output(command: str) -> dict[str, Any]:
    """Build Codex's supported PreToolUse rewrite payload."""
    return {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "allow",
            "updatedInput": {"command": f"rtk {command.strip()}"},
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

    if not should_wrap_command(command):
        return None

    return build_rewrite_output(command)


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

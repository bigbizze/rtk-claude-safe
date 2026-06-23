"""Claude Code hook wrapper that applies fail-open safety checks before rtk."""

from __future__ import annotations

import json
import sys
from typing import Any, TextIO

from rtk_claude_safe.allowlist import rewrite_command_for_agent


def build_rewrite_output(command: str) -> dict[str, Any]:
    """Build Claude Code's PreToolUse rewrite payload."""
    return {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecisionReason": "RTK safe rewrite",
            "updatedInput": {"command": command},
        }
    }


def maybe_rewrite_payload(payload: Any) -> dict[str, Any] | None:
    """Return a Claude rewrite payload, or None to fail open."""
    if not isinstance(payload, dict):
        return None
    tool_input = payload.get("tool_input")
    if not isinstance(tool_input, dict):
        return None
    command = tool_input.get("command")
    if not isinstance(command, str):
        return None

    rewrite = rewrite_command_for_agent(command, "claude")
    if rewrite is None:
        return None
    return build_rewrite_output(rewrite)


def main(stdin: TextIO | None = None, stdout: TextIO | None = None) -> int:
    """Run the safe Claude wrapper. Uncertain inputs intentionally do nothing."""
    stdin = stdin or sys.stdin
    stdout = stdout or sys.stdout
    raw_payload = stdin.read()

    try:
        payload = json.loads(raw_payload)
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

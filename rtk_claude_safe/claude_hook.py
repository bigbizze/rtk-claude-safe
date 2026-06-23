"""Claude Code hook wrapper that applies fail-open safety checks before rtk."""

from __future__ import annotations

import json
import subprocess
import sys
from typing import Any, TextIO

from rtk_claude_safe.allowlist import should_wrap_command


def should_run_rtk_hook(payload: Any) -> bool:
    """Return True when a Claude Bash hook payload should be delegated to rtk."""
    if not isinstance(payload, dict):
        return False
    tool_input = payload.get("tool_input")
    if not isinstance(tool_input, dict):
        return False
    command = tool_input.get("command")
    return isinstance(command, str) and should_wrap_command(command)


def main(stdin: TextIO | None = None) -> int:
    """Run the safe Claude wrapper. Uncertain inputs intentionally do nothing."""
    stdin = stdin or sys.stdin
    raw_payload = stdin.read()

    try:
        payload = json.loads(raw_payload)
    except json.JSONDecodeError:
        return 0

    if not should_run_rtk_hook(payload):
        return 0

    try:
        completed = subprocess.run(["rtk", "hook", "claude"], input=raw_payload, text=True)
    except FileNotFoundError:
        print("[rtk-claude-safe] warning: rtk was not found on PATH", file=sys.stderr)
        return 0
    return completed.returncode


if __name__ == "__main__":
    raise SystemExit(main())

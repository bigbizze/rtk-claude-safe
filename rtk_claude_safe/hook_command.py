"""Build stable hook command strings for agent settings files."""

from __future__ import annotations

import shlex
import shutil
import sys
from pathlib import Path

_CONSOLE_SCRIPT_NAMES = {"rtk-claude-safe", "rtk-claude-safe.exe"}


def build_hook_command(subcommand: str, script: str | None = None) -> str:
    """Return an absolute command string for an agent hook subcommand."""
    script_path = _resolve_console_script(script)
    if script_path is not None:
        return f"{shlex.quote(str(script_path))} {subcommand}"
    return f"{shlex.quote(sys.executable)} -m rtk_claude_safe {subcommand}"


def _resolve_console_script(script: str | None = None) -> Path | None:
    if script:
        return Path(script).expanduser().resolve()

    active = Path(sys.argv[0])
    if active.name in _CONSOLE_SCRIPT_NAMES:
        if active.is_absolute() or active.parent != Path("."):
            return active.expanduser().resolve()
        found = shutil.which(str(active)) or shutil.which(active.name)
        if found:
            return Path(found).resolve()

    # When invoked as `python -m rtk_claude_safe`, keep the hook tied to that
    # interpreter instead of falling through to an older PATH installation.
    if active.name == "__main__.py" and "rtk_claude_safe" in active.parts:
        return None

    found = shutil.which("rtk-claude-safe")
    if found:
        return Path(found).resolve()
    return None

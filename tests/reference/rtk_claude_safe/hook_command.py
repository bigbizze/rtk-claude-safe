"""Build stable hook command strings for agent settings files."""

from __future__ import annotations

import shlex
import shutil
import subprocess
import sys
import ntpath
import os
import platform
from pathlib import Path

_CONSOLE_SCRIPT_NAMES = {"rtk-claude-safe", "rtk-claude-safe.exe"}


def build_hook_command(subcommand: str, script: str | None = None) -> str:
    """Return an absolute command string for an agent hook subcommand."""
    script_path = _resolve_console_script(script)
    if script_path is not None:
        return _join_command([script_path, subcommand])
    return _join_command([_normalize_hook_path(sys.executable), "-m", "rtk_claude_safe", subcommand])


def _join_command(parts: list[str]) -> str:
    if platform.system() == "Windows":
        return subprocess.list2cmdline(parts)
    return " ".join(shlex.quote(part) for part in parts)


def _normalize_hook_path(path: str, system: str | None = None) -> str:
    system = platform.system() if system is None else system
    if system == "Windows":
        return ntpath.normpath(ntpath.abspath(os.fspath(path)))
    return str(Path(path).expanduser().resolve())


def _resolve_console_script(script: str | None = None) -> str | None:
    if script:
        return _normalize_hook_path(script)

    active_raw = sys.argv[0]
    active_name = ntpath.basename(os.path.basename(active_raw))
    if active_name in _CONSOLE_SCRIPT_NAMES:
        active = Path(active_raw)
        if active.is_absolute() or active.parent != Path(".") or ntpath.isabs(active_raw):
            return _normalize_hook_path(active_raw)
        found = shutil.which(active_raw) or shutil.which(active_name)
        if found:
            return _normalize_hook_path(found)

    # When invoked as `python -m rtk_claude_safe`, keep the hook tied to that
    # interpreter instead of falling through to an older PATH installation.
    active = Path(active_raw)
    if active.name == "__main__.py" and "rtk_claude_safe" in active.parts:
        return None

    found = shutil.which("rtk-claude-safe")
    if found:
        return _normalize_hook_path(found)
    return None

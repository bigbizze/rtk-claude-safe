"""Classify managed hook commands without substring matching."""

from __future__ import annotations

import os
import ntpath
import posixpath
import re
import shlex
from typing import Literal

ManagedHook = Literal["claude", "codex"]

_ASSIGNMENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")
_PYTHON_RE = re.compile(r"^python(?:\d+(?:\.\d+)?)?(?:\.exe)?$", re.IGNORECASE)


def is_managed_hook_command(command: str, hook: ManagedHook) -> bool:
    """Return True only for exact commands managed by this package."""
    for tokens in _split_command_variants(command):
        if _is_safe_wrapper(tokens, hook) or (hook == "claude" and _is_rtk_claude_hook(tokens)):
            return True
    return False


def _split_command_variants(command: str) -> list[list[str]]:
    variants: list[list[str]] = []
    seen: set[tuple[str, ...]] = set()
    for posix in (True, False):
        try:
            tokens = shlex.split(command, posix=posix)
        except ValueError:
            continue
        key = tuple(tokens)
        if tokens and key not in seen:
            variants.append(tokens)
            seen.add(key)
    return variants


def _basename(value: str) -> str:
    cleaned = value.strip("\"'")
    return posixpath.basename(ntpath.basename(os.path.basename(cleaned))).lower()


def _strip_env_and_assignments(tokens: list[str]) -> list[str]:
    remaining = list(tokens)
    while remaining and _ASSIGNMENT_RE.match(remaining[0]):
        remaining.pop(0)
    if remaining and _basename(remaining[0]) in {"env", "env.exe"}:
        remaining.pop(0)
        while remaining and _ASSIGNMENT_RE.match(remaining[0]):
            remaining.pop(0)
    return remaining


def _is_rtk_claude_hook(tokens: list[str]) -> bool:
    remaining = _strip_env_and_assignments(tokens)
    if len(remaining) != 3:
        return False
    return _basename(remaining[0]) in {"rtk", "rtk.exe"} and remaining[1:] == ["hook", "claude"]


def _is_safe_wrapper(tokens: list[str], hook: ManagedHook) -> bool:
    subcommand = f"{hook}-hook"
    if len(tokens) == 2 and _basename(tokens[0]) in {"rtk-claude-safe", "rtk-claude-safe.exe"}:
        return tokens[1] == subcommand
    if (
        len(tokens) == 4
        and _PYTHON_RE.match(_basename(tokens[0]))
        and tokens[1:3] == ["-m", "rtk_claude_safe"]
    ):
        return tokens[3] == subcommand
    return False

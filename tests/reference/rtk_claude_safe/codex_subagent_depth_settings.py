"""Install and remove Codex subagent depth enforcement hooks."""

from __future__ import annotations

import copy
import json
import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any

from rtk_claude_safe.hook_command import build_hook_command
from rtk_claude_safe.managed_hooks import is_managed_hook_command

CODEX_AGENT_MATCHER = "^Agent$"
SUBAGENT_DEPTH_HOOK_TIMEOUT = 10
SUBAGENT_DEPTH_SESSION_STATUS_MESSAGE = "Subagent depth policy"
SUBAGENT_DEPTH_PRE_STATUS_MESSAGE = "Subagent depth gate"
SUBAGENT_DEPTH_POST_STATUS_MESSAGE = "Subagent depth record"
SUPPORTED_CODEX_MIN_VERSION = (0, 144, 6)
SUPPORTED_CODEX_MAX_VERSION = (0, 145, 0)
SUPPORTED_CODEX_VERSION_RANGE = ">=0.144.6,<0.145.0"


class CodexVersionError(RuntimeError):
    """Raised when the installed Codex CLI is not supported."""


def default_codex_home() -> Path:
    """Return the Codex home used by the local CLI."""
    return Path(os.environ.get("CODEX_HOME", Path.home() / ".codex")).expanduser()


def default_hooks_path(codex_home: Path | None = None) -> Path:
    """Return the user-level Codex hooks.json path."""
    return (codex_home or default_codex_home()) / "hooks.json"


def default_config_path(codex_home: Path | None = None) -> Path:
    """Return the user-level Codex config.toml path."""
    return (codex_home or default_codex_home()) / "config.toml"


def build_subagent_depth_hook_command(script: str | None = None) -> str:
    """Return the stable command Codex should run for the depth hook."""
    return build_hook_command("subagent-depth-hook", script)


def build_subagent_depth_hook_entry(
    *,
    command: str | None = None,
    status_message: str,
) -> dict[str, Any]:
    """Build one Codex command hook entry for subagent depth enforcement."""
    return {
        "type": "command",
        "command": command or build_subagent_depth_hook_command(),
        "timeout": SUBAGENT_DEPTH_HOOK_TIMEOUT,
        "statusMessage": status_message,
    }


def assert_supported_codex_cli() -> str:
    """Return `codex --version` output when this Codex CLI is supported."""
    if shutil.which("codex") is None:
        raise CodexVersionError("codex executable was not found on PATH")
    try:
        result = subprocess.run(
            ["codex", "--version"],
            text=True,
            capture_output=True,
            check=False,
            timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired) as e:
        raise CodexVersionError(f"failed to run codex --version: {e}") from e
    version_text = (result.stdout or result.stderr).strip()
    if result.returncode != 0:
        raise CodexVersionError(f"codex --version failed: {version_text}")
    version = parse_codex_cli_version(version_text)
    if version is None:
        raise CodexVersionError(f"could not parse codex version from: {version_text!r}")
    if not (SUPPORTED_CODEX_MIN_VERSION <= version < SUPPORTED_CODEX_MAX_VERSION):
        raise CodexVersionError(
            "Codex subagent depth enforcement supports Codex "
            f"{SUPPORTED_CODEX_VERSION_RANGE}; found {version_text}"
        )
    return version_text


def parse_codex_cli_version(version_text: str) -> tuple[int, int, int] | None:
    """Parse `codex-cli 0.144.6` style version output."""
    match = re.search(r"\b(\d+)\.(\d+)\.(\d+)\b", version_text)
    if match is None:
        return None
    major, minor, patch = match.groups()
    return int(major), int(minor), int(patch)


def _load(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    text = path.read_text(encoding="utf-8").strip()
    if not text:
        return {}
    try:
        data = json.loads(text)
    except json.JSONDecodeError as e:
        raise ValueError(f"{path} is not valid JSON: {e.msg}") from e
    if not isinstance(data, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return data


def _is_rtk_subagent_depth_hook(entry: Any) -> bool:
    if not isinstance(entry, dict):
        return False
    if entry.get("type") != "command":
        return False
    command = entry.get("command")
    if not isinstance(command, str):
        return False
    return is_managed_hook_command(command, "codex-subagent-depth")


def _event_groups(hooks_root: dict[str, Any], event_name: str, path: Path) -> list[dict[str, Any]]:
    event = hooks_root.setdefault(event_name, [])
    if not isinstance(event, list):
        raise ValueError(f"hooks.{event_name} in {path} is not a list")
    return event


def _remove_managed_entries_from_event(
    hooks_root: dict[str, Any],
    event_name: str,
    path: Path,
) -> None:
    event = hooks_root.get(event_name)
    if event is None:
        return
    if not isinstance(event, list):
        raise ValueError(f"hooks.{event_name} in {path} is not a list")

    kept_groups: list[Any] = []
    for group in event:
        if not isinstance(group, dict):
            kept_groups.append(group)
            continue
        hooks = group.get("hooks")
        if hooks is None:
            group["hooks"] = []
            kept_groups.append(group)
            continue
        if not isinstance(hooks, list):
            raise ValueError(f"hooks.{event_name} group in {path} has non-list hooks")
        group["hooks"] = [entry for entry in hooks if not _is_rtk_subagent_depth_hook(entry)]
        if group["hooks"] or set(group) - {"matcher", "hooks"}:
            kept_groups.append(group)

    if kept_groups:
        hooks_root[event_name] = kept_groups
    else:
        hooks_root.pop(event_name, None)


def _append_group(
    hooks_root: dict[str, Any],
    event_name: str,
    hook_entry: dict[str, Any],
    path: Path,
    *,
    matcher: str | None = None,
) -> None:
    groups = _event_groups(hooks_root, event_name, path)
    group: dict[str, Any] = {"hooks": [hook_entry]}
    if matcher is not None:
        group["matcher"] = matcher
    groups.append(group)


def patch_subagent_depth_hooks(
    path: Path | None = None,
    *,
    command: str | None = None,
) -> bool:
    """Patch user-level Codex hooks.json for subagent depth enforcement."""
    path = path or default_hooks_path()
    hooks_file = _load(path)
    original = copy.deepcopy(hooks_file)
    hooks_root = hooks_file.setdefault("hooks", {})
    if not isinstance(hooks_root, dict):
        raise ValueError(f"hooks in {path} is not an object")

    for event_name in ("SessionStart", "PreToolUse", "PostToolUse"):
        _remove_managed_entries_from_event(hooks_root, event_name, path)

    hook_command = command or build_subagent_depth_hook_command()
    _append_group(
        hooks_root,
        "SessionStart",
        build_subagent_depth_hook_entry(
            command=hook_command,
            status_message=SUBAGENT_DEPTH_SESSION_STATUS_MESSAGE,
        ),
        path,
    )
    _append_group(
        hooks_root,
        "PreToolUse",
        build_subagent_depth_hook_entry(
            command=hook_command,
            status_message=SUBAGENT_DEPTH_PRE_STATUS_MESSAGE,
        ),
        path,
        matcher=CODEX_AGENT_MATCHER,
    )
    _append_group(
        hooks_root,
        "PostToolUse",
        build_subagent_depth_hook_entry(
            command=hook_command,
            status_message=SUBAGENT_DEPTH_POST_STATUS_MESSAGE,
        ),
        path,
        matcher=CODEX_AGENT_MATCHER,
    )

    changed = hooks_file != original
    if changed:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(hooks_file, indent=2) + "\n", encoding="utf-8")
    return changed


def remove_subagent_depth_hooks(path: Path | None = None) -> bool:
    """Remove only rtk-claude-safe subagent-depth hook entries."""
    path = path or default_hooks_path()
    if not path.exists():
        return False

    hooks_file = _load(path)
    original = copy.deepcopy(hooks_file)
    hooks_root = hooks_file.get("hooks")
    if hooks_root is None:
        return False
    if not isinstance(hooks_root, dict):
        raise ValueError(f"hooks in {path} is not an object")

    for event_name in ("SessionStart", "PreToolUse", "PostToolUse"):
        _remove_managed_entries_from_event(hooks_root, event_name, path)

    if not hooks_root:
        hooks_file.pop("hooks", None)

    changed = hooks_file != original
    if changed:
        path.write_text(json.dumps(hooks_file, indent=2) + "\n", encoding="utf-8")
    return changed

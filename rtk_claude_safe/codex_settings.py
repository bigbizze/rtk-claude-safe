"""Patch ~/.codex/hooks.json with the rtk-claude-safe Codex hook."""

from __future__ import annotations

import copy
import json
import re
import shlex
import shutil
import sys
from pathlib import Path
from typing import Any

DEFAULT_CODEX_HOME = Path.home() / ".codex"
DEFAULT_CODEX_HOOKS_PATH = DEFAULT_CODEX_HOME / "hooks.json"
DEFAULT_CODEX_CONFIG_PATH = DEFAULT_CODEX_HOME / "config.toml"

CODEX_BASH_MATCHER = "^Bash$"
CODEX_HOOK_TIMEOUT = 30
CODEX_HOOK_STATUS_MESSAGE = "RTK safe rewrite"


def build_codex_hook_command(script: str | None = None) -> str:
    """Return the stable command Codex should run for the hook."""
    script = script or shutil.which("rtk-claude-safe")
    if script:
        return f"{shlex.quote(str(Path(script).resolve()))} codex-hook"
    return f"{shlex.quote(sys.executable)} -m rtk_claude_safe codex-hook"


def build_codex_hook_entry(command: str | None = None) -> dict[str, Any]:
    """Build the Codex command hook entry."""
    return {
        "type": "command",
        "command": command or build_codex_hook_command(),
        "timeout": CODEX_HOOK_TIMEOUT,
        "statusMessage": CODEX_HOOK_STATUS_MESSAGE,
    }


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


def _is_rtk_codex_hook(entry: Any) -> bool:
    if not isinstance(entry, dict):
        return False
    if entry.get("type") != "command":
        return False
    command = entry.get("command")
    if not isinstance(command, str):
        return False
    return "codex-hook" in command and (
        "rtk-claude-safe" in command or "rtk_claude_safe" in command
    )


def _patch_bash_group(group: dict[str, Any], hook_entry: dict[str, Any], path: Path) -> None:
    hooks = group.get("hooks")
    if hooks is None:
        group["hooks"] = [hook_entry]
        return
    if not isinstance(hooks, list):
        raise ValueError(f"hooks.PreToolUse matcher {CODEX_BASH_MATCHER!r} in {path} has non-list hooks")

    kept = [entry for entry in hooks if not _is_rtk_codex_hook(entry)]
    kept.append(hook_entry)
    group["hooks"] = kept


def patch_codex_hooks(
    path: Path = DEFAULT_CODEX_HOOKS_PATH,
    command: str | None = None,
) -> bool:
    """Patch Codex hooks.json. Returns True if the file changed."""
    if not path.parent.exists():
        return False

    hooks_file = _load(path)
    original = copy.deepcopy(hooks_file)
    hooks_root = hooks_file.setdefault("hooks", {})
    if not isinstance(hooks_root, dict):
        raise ValueError(f"hooks in {path} is not an object")

    pre_tool_use = hooks_root.setdefault("PreToolUse", [])
    if not isinstance(pre_tool_use, list):
        raise ValueError(f"hooks.PreToolUse in {path} is not a list")

    hook_entry = build_codex_hook_entry(command)
    bash_group = None
    for group in pre_tool_use:
        if isinstance(group, dict) and group.get("matcher") == CODEX_BASH_MATCHER:
            bash_group = group
            break

    if bash_group is None:
        pre_tool_use.append({"matcher": CODEX_BASH_MATCHER, "hooks": [hook_entry]})
    else:
        _patch_bash_group(bash_group, hook_entry, path)

    changed = hooks_file != original
    if changed:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(hooks_file, indent=2) + "\n", encoding="utf-8")
    return changed


def inspect_codex_config(path: Path = DEFAULT_CODEX_CONFIG_PATH) -> list[str]:
    """Return warnings for Codex TOML settings we intentionally do not edit."""
    if not path.exists():
        return []

    text = path.read_text(encoding="utf-8")
    warnings: list[str] = []

    if re.search(r"(?m)^\s*\[hooks\]\s*$", text) or re.search(
        r"(?m)^\s*\[\[hooks\.", text
    ):
        warnings.append(
            f"{path} appears to contain inline hooks; Codex may merge them with hooks.json and warn at startup."
        )

    in_features = False
    for raw_line in text.splitlines():
        line = raw_line.split("#", 1)[0].strip()
        if not line:
            continue
        if line.startswith("[") and line.endswith("]"):
            in_features = line == "[features]"
            continue
        if in_features and re.match(r"hooks\s*=\s*false\b", line, re.IGNORECASE):
            warnings.append(f"{path} has [features] hooks = false; Codex will not run hooks.")
            break

    return warnings

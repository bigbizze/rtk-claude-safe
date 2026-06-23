"""Patch ~/.claude/settings.json so rtk's PreToolUse hook is scoped, not catch-all."""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

from rtk_claude_safe.allowlist import build_claude_scoped_hooks

DEFAULT_SETTINGS_PATH = Path.home() / ".claude" / "settings.json"
RTK_COMMAND = "rtk hook claude"


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


def _is_rtk_hook(entry: Any) -> bool:
    return (
        isinstance(entry, dict)
        and entry.get("type") == "command"
        and entry.get("command") == RTK_COMMAND
    )


def _remove_rtk_hooks_from_bash_matcher(matcher_entry: dict[str, Any], path: Path) -> None:
    hooks = matcher_entry.get("hooks")
    if hooks is None:
        matcher_entry["hooks"] = []
        return
    if not isinstance(hooks, list):
        raise ValueError(f"hooks.PreToolUse matcher 'Bash' in {path} has non-list hooks")
    matcher_entry["hooks"] = [h for h in hooks if not _is_rtk_hook(h)]


def patch_settings(path: Path = DEFAULT_SETTINGS_PATH) -> bool:
    """Edit `path` in place. Returns True if the file was changed."""
    settings = _load(path)
    original = copy.deepcopy(settings)
    hooks_root = settings.setdefault("hooks", {})
    if not isinstance(hooks_root, dict):
        raise ValueError(f"hooks in {path} is not an object")

    pre_tool_use = hooks_root.setdefault("PreToolUse", [])
    if not isinstance(pre_tool_use, list):
        raise ValueError(f"hooks.PreToolUse in {path} is not a list")

    bash_entries = []
    for entry in pre_tool_use:
        if isinstance(entry, dict) and entry.get("matcher") == "Bash":
            bash_entries.append(entry)

    scoped_hooks = build_claude_scoped_hooks(RTK_COMMAND)
    if not bash_entries:
        pre_tool_use.append({"matcher": "Bash", "hooks": scoped_hooks})
    else:
        for entry in bash_entries:
            _remove_rtk_hooks_from_bash_matcher(entry, path)
        bash_entries[0]["hooks"].extend(scoped_hooks)

    changed = settings != original
    if changed:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(settings, indent=2) + "\n", encoding="utf-8")
    return changed

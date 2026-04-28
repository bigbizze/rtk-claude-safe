"""Patch ~/.claude/settings.json so rtk's PreToolUse hook is scoped, not catch-all."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from rtk_claude_safe.hooks import build_scoped_hooks

DEFAULT_SETTINGS_PATH = Path.home() / ".claude" / "settings.json"
RTK_COMMAND = "rtk hook claude"


def _load(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    text = path.read_text(encoding="utf-8").strip()
    if not text:
        return {}
    return json.loads(text)


def _is_rtk_hook(entry: Any) -> bool:
    return (
        isinstance(entry, dict)
        and entry.get("type") == "command"
        and entry.get("command") == RTK_COMMAND
    )


def _has_scoped_set(hooks: list[Any]) -> bool:
    """Return True if every rtk hook in the list already carries an `if` clause."""
    rtk_entries = [h for h in hooks if _is_rtk_hook(h)]
    return bool(rtk_entries) and all("if" in h for h in rtk_entries)


def _patch_bash_matcher(matcher_entry: dict[str, Any]) -> bool:
    """Replace bare rtk hooks under this matcher with the scoped list. Return True if changed."""
    hooks = matcher_entry.get("hooks")
    if not isinstance(hooks, list):
        matcher_entry["hooks"] = build_scoped_hooks(RTK_COMMAND)
        return True

    if _has_scoped_set(hooks):
        return False

    # Drop every existing rtk hook (with or without `if`) so we rebuild deterministically.
    kept = [h for h in hooks if not _is_rtk_hook(h)]
    kept.extend(build_scoped_hooks(RTK_COMMAND))
    matcher_entry["hooks"] = kept
    return True


def patch_settings(path: Path = DEFAULT_SETTINGS_PATH) -> bool:
    """Edit `path` in place. Returns True if the file was changed."""
    settings = _load(path)
    hooks_root = settings.setdefault("hooks", {})
    pre_tool_use = hooks_root.setdefault("PreToolUse", [])
    if not isinstance(pre_tool_use, list):
        raise ValueError(f"hooks.PreToolUse in {path} is not a list")

    bash_entry = None
    for entry in pre_tool_use:
        if isinstance(entry, dict) and entry.get("matcher") == "Bash":
            bash_entry = entry
            break

    if bash_entry is None:
        bash_entry = {"matcher": "Bash", "hooks": build_scoped_hooks(RTK_COMMAND)}
        pre_tool_use.append(bash_entry)
        changed = True
    else:
        changed = _patch_bash_matcher(bash_entry)

    if changed:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(settings, indent=2) + "\n", encoding="utf-8")
    return changed

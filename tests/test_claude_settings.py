from __future__ import annotations

import json

import pytest

from rtk_claude_safe.allowlist import SCOPED_PATTERNS, build_claude_scoped_hooks
from rtk_claude_safe.claude_settings import RTK_COMMAND, patch_settings

SAFE_CLAUDE_COMMAND = "rtk-claude-safe claude-hook"


def _read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def _rtk_entries(data, command: str):
    entries = []
    for group in data["hooks"]["PreToolUse"]:
        if isinstance(group, dict) and group.get("matcher") == "Bash":
            entries.extend(
                hook
                for hook in group.get("hooks", [])
                if isinstance(hook, dict) and hook.get("command") == command
            )
    return entries


def test_claude_settings_create_scoped_hooks_from_scratch(tmp_path) -> None:
    settings_path = tmp_path / ".claude" / "settings.json"

    assert patch_settings(settings_path, command=SAFE_CLAUDE_COMMAND)

    data = _read(settings_path)
    assert data["hooks"]["PreToolUse"] == [
        {"matcher": "Bash", "hooks": build_claude_scoped_hooks(SAFE_CLAUDE_COMMAND)}
    ]
    assert not patch_settings(settings_path, command=SAFE_CLAUDE_COMMAND)


def test_claude_settings_remove_rtk_hooks_from_duplicate_bash_groups(tmp_path) -> None:
    settings_path = tmp_path / "settings.json"
    settings_path.write_text(
        json.dumps(
            {
                "hooks": {
                    "PreToolUse": [
                        {
                            "matcher": "Bash",
                            "hooks": [
                                {"type": "command", "command": "user-hook"},
                                {"type": "command", "command": RTK_COMMAND},
                            ],
                        },
                        {
                            "matcher": "Bash",
                            "hooks": [{"type": "command", "command": RTK_COMMAND}],
                        },
                    ]
                }
            }
        ),
        encoding="utf-8",
    )

    assert patch_settings(settings_path, command=SAFE_CLAUDE_COMMAND)

    data = _read(settings_path)
    assert data["hooks"]["PreToolUse"][0]["hooks"][0] == {
        "type": "command",
        "command": "user-hook",
    }
    assert data["hooks"]["PreToolUse"][1]["hooks"] == []
    rtk_entries = _rtk_entries(data, SAFE_CLAUDE_COMMAND)
    assert len(rtk_entries) == len(SCOPED_PATTERNS)
    assert rtk_entries == build_claude_scoped_hooks(SAFE_CLAUDE_COMMAND)


def test_claude_settings_reconcile_stale_scoped_hooks(tmp_path) -> None:
    settings_path = tmp_path / "settings.json"
    settings_path.write_text(
        json.dumps(
            {
                "hooks": {
                    "PreToolUse": [
                        {
                            "matcher": "Bash",
                            "hooks": [
                                {"type": "command", "command": RTK_COMMAND, "if": "Bash(old*)"},
                                {"type": "command", "command": "user-hook"},
                            ],
                        }
                    ]
                }
            }
        ),
        encoding="utf-8",
    )

    assert patch_settings(settings_path, command=SAFE_CLAUDE_COMMAND)

    hooks = _read(settings_path)["hooks"]["PreToolUse"][0]["hooks"]
    assert {"type": "command", "command": RTK_COMMAND, "if": "Bash(old*)"} not in hooks
    assert {"type": "command", "command": "user-hook"} in hooks
    assert hooks[1:] == build_claude_scoped_hooks(SAFE_CLAUDE_COMMAND)


def test_claude_settings_replace_previous_version_scoped_hooks(tmp_path) -> None:
    settings_path = tmp_path / "settings.json"
    settings_path.write_text(
        json.dumps(
            {
                "hooks": {
                    "PreToolUse": [
                        {
                            "matcher": "Bash",
                            "hooks": build_claude_scoped_hooks(RTK_COMMAND),
                        }
                    ]
                }
            }
        ),
        encoding="utf-8",
    )

    assert patch_settings(settings_path, command=SAFE_CLAUDE_COMMAND)
    assert not patch_settings(settings_path, command=SAFE_CLAUDE_COMMAND)

    hooks = _read(settings_path)["hooks"]["PreToolUse"][0]["hooks"]
    assert hooks == build_claude_scoped_hooks(SAFE_CLAUDE_COMMAND)


def test_claude_settings_remove_stale_safe_wrapper_paths(tmp_path) -> None:
    settings_path = tmp_path / "settings.json"
    settings_path.write_text(
        json.dumps(
            {
                "hooks": {
                    "PreToolUse": [
                        {
                            "matcher": "Bash",
                            "hooks": [
                                {
                                    "type": "command",
                                    "command": "/old/rtk-claude-safe claude-hook",
                                    "if": "Bash(git status*)",
                                }
                            ],
                        }
                    ]
                }
            }
        ),
        encoding="utf-8",
    )

    assert patch_settings(settings_path, command=SAFE_CLAUDE_COMMAND)

    hooks = _read(settings_path)["hooks"]["PreToolUse"][0]["hooks"]
    assert all(hook["command"] == SAFE_CLAUDE_COMMAND for hook in hooks)
    assert hooks == build_claude_scoped_hooks(SAFE_CLAUDE_COMMAND)


@pytest.mark.parametrize(
    ("content", "message"),
    [
        ("[]", "must contain a JSON object"),
        ('{"hooks": []}', "hooks in .* is not an object"),
        ('{"hooks": {"PreToolUse": {}}}', "hooks.PreToolUse .* is not a list"),
        (
            '{"hooks": {"PreToolUse": [{"matcher": "Bash", "hooks": {}}]}}',
            "non-list hooks",
        ),
    ],
)
def test_claude_settings_malformed_shapes_raise_value_error(
    tmp_path, content: str, message: str
) -> None:
    settings_path = tmp_path / "settings.json"
    settings_path.write_text(content, encoding="utf-8")

    with pytest.raises(ValueError, match=message):
        patch_settings(settings_path, command=SAFE_CLAUDE_COMMAND)

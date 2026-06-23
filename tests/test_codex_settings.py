from __future__ import annotations

import json

import pytest

from rtk_claude_safe.codex_settings import (
    CODEX_BASH_MATCHER,
    inspect_codex_config,
    patch_codex_hooks,
)


def test_codex_hooks_not_created_when_parent_missing(tmp_path) -> None:
    hooks_path = tmp_path / ".codex" / "hooks.json"

    assert not patch_codex_hooks(hooks_path, command="/bin/rtk-claude-safe codex-hook")
    assert not hooks_path.exists()


def test_codex_hooks_created_from_scratch(tmp_path) -> None:
    codex_home = tmp_path / ".codex"
    codex_home.mkdir()
    hooks_path = codex_home / "hooks.json"

    assert patch_codex_hooks(hooks_path, command="/bin/rtk-claude-safe codex-hook")

    data = json.loads(hooks_path.read_text(encoding="utf-8"))
    pre_tool_use = data["hooks"]["PreToolUse"]
    assert pre_tool_use == [
        {
            "matcher": CODEX_BASH_MATCHER,
            "hooks": [
                {
                    "type": "command",
                    "command": "/bin/rtk-claude-safe codex-hook",
                    "timeout": 30,
                    "statusMessage": "RTK safe rewrite",
                }
            ],
        }
    ]


def test_codex_hooks_preserve_unrelated_hooks(tmp_path) -> None:
    codex_home = tmp_path / ".codex"
    codex_home.mkdir()
    hooks_path = codex_home / "hooks.json"
    original = {
        "hooks": {
            "SessionStart": [{"matcher": "startup", "hooks": [{"type": "command", "command": "echo hi"}]}],
            "PreToolUse": [
                {"matcher": "apply_patch", "hooks": [{"type": "command", "command": "other"}]},
                {
                    "matcher": CODEX_BASH_MATCHER,
                    "hooks": [{"type": "command", "command": "user-hook"}],
                },
            ],
        }
    }
    hooks_path.write_text(json.dumps(original), encoding="utf-8")

    assert patch_codex_hooks(hooks_path, command="/bin/rtk-claude-safe codex-hook")

    data = json.loads(hooks_path.read_text(encoding="utf-8"))
    assert data["hooks"]["SessionStart"] == original["hooks"]["SessionStart"]
    assert data["hooks"]["PreToolUse"][0] == original["hooks"]["PreToolUse"][0]
    bash_hooks = data["hooks"]["PreToolUse"][1]["hooks"]
    assert {"type": "command", "command": "user-hook"} in bash_hooks
    assert any(hook.get("command") == "/bin/rtk-claude-safe codex-hook" for hook in bash_hooks)


def test_codex_hooks_are_idempotent(tmp_path) -> None:
    codex_home = tmp_path / ".codex"
    codex_home.mkdir()
    hooks_path = codex_home / "hooks.json"

    assert patch_codex_hooks(hooks_path, command="/bin/rtk-claude-safe codex-hook")
    assert not patch_codex_hooks(hooks_path, command="/bin/rtk-claude-safe codex-hook")


def test_codex_hooks_update_stale_command_path(tmp_path) -> None:
    codex_home = tmp_path / ".codex"
    codex_home.mkdir()
    hooks_path = codex_home / "hooks.json"
    hooks_path.write_text(
        json.dumps(
            {
                "hooks": {
                    "PreToolUse": [
                        {
                            "matcher": CODEX_BASH_MATCHER,
                            "hooks": [
                                {"type": "command", "command": "/old/rtk-claude-safe codex-hook"}
                            ],
                        }
                    ]
                }
            }
        ),
        encoding="utf-8",
    )

    assert patch_codex_hooks(hooks_path, command="/new/rtk-claude-safe codex-hook")

    data = json.loads(hooks_path.read_text(encoding="utf-8"))
    commands = [hook["command"] for hook in data["hooks"]["PreToolUse"][0]["hooks"]]
    assert commands == ["/new/rtk-claude-safe codex-hook"]


def test_codex_hooks_error_cleanly_on_malformed_json(tmp_path) -> None:
    codex_home = tmp_path / ".codex"
    codex_home.mkdir()
    hooks_path = codex_home / "hooks.json"
    hooks_path.write_text("{not json", encoding="utf-8")

    with pytest.raises(ValueError, match="not valid JSON"):
        patch_codex_hooks(hooks_path, command="/bin/rtk-claude-safe codex-hook")


def test_codex_config_warnings(tmp_path) -> None:
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        """
[features]
hooks = false

[[hooks.PreToolUse]]
matcher = "^Bash$"
""",
        encoding="utf-8",
    )

    warnings = inspect_codex_config(config_path)
    assert len(warnings) == 2
    assert "inline hooks" in warnings[0]
    assert "hooks = false" in warnings[1]

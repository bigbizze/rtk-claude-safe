from __future__ import annotations

import json

import pytest

from rtk_claude_safe.codex_subagent_depth_settings import (
    CODEX_AGENT_MATCHER,
    parse_codex_cli_version,
    patch_subagent_depth_hooks,
    remove_subagent_depth_hooks,
)


def _commands(path) -> list[str]:
    data = json.loads(path.read_text(encoding="utf-8"))
    return [
        hook["command"]
        for groups in data["hooks"].values()
        for group in groups
        for hook in group.get("hooks", [])
        if isinstance(hook, dict) and "command" in hook
    ]


def test_subagent_depth_hooks_created_from_scratch(tmp_path) -> None:
    hooks_path = tmp_path / ".codex" / "hooks.json"
    hooks_path.parent.mkdir()

    assert patch_subagent_depth_hooks(hooks_path, command="/bin/rtk-claude-safe subagent-depth-hook")

    data = json.loads(hooks_path.read_text(encoding="utf-8"))
    assert data["hooks"]["SessionStart"] == [
        {
            "hooks": [
                {
                    "type": "command",
                    "command": "/bin/rtk-claude-safe subagent-depth-hook",
                    "timeout": 10,
                    "statusMessage": "Subagent depth policy",
                }
            ]
        }
    ]
    assert data["hooks"]["PreToolUse"][0]["matcher"] == CODEX_AGENT_MATCHER
    assert data["hooks"]["PostToolUse"][0]["matcher"] == CODEX_AGENT_MATCHER


@pytest.mark.parametrize(
    ("version_text", "parsed"),
    [
        ("codex-cli 0.144.6", (0, 144, 6)),
        ("codex-cli 0.145.0", (0, 145, 0)),
        ("not a version", None),
    ],
)
def test_parse_codex_cli_version(version_text: str, parsed: tuple[int, int, int] | None) -> None:
    assert parse_codex_cli_version(version_text) == parsed


def test_subagent_depth_hooks_are_idempotent(tmp_path) -> None:
    hooks_path = tmp_path / ".codex" / "hooks.json"
    hooks_path.parent.mkdir()

    assert patch_subagent_depth_hooks(hooks_path, command="/bin/rtk-claude-safe subagent-depth-hook")
    assert not patch_subagent_depth_hooks(
        hooks_path,
        command="/bin/rtk-claude-safe subagent-depth-hook",
    )


def test_subagent_depth_hooks_preserve_unrelated_hooks(tmp_path) -> None:
    hooks_path = tmp_path / ".codex" / "hooks.json"
    hooks_path.parent.mkdir()
    original = {
        "description": "keep me",
        "hooks": {
            "SessionStart": [{"hooks": [{"type": "command", "command": "echo session"}]}],
            "PreToolUse": [{"matcher": "^Bash$", "hooks": [{"type": "command", "command": "echo pre"}]}],
            "PostToolUse": [{"matcher": "^Bash$", "hooks": [{"type": "command", "command": "echo post"}]}],
        },
    }
    hooks_path.write_text(json.dumps(original), encoding="utf-8")

    assert patch_subagent_depth_hooks(hooks_path, command="/bin/rtk-claude-safe subagent-depth-hook")

    data = json.loads(hooks_path.read_text(encoding="utf-8"))
    assert data["description"] == "keep me"
    assert {"type": "command", "command": "echo session"} in data["hooks"]["SessionStart"][0]["hooks"]
    assert data["hooks"]["PreToolUse"][0] == original["hooks"]["PreToolUse"][0]
    assert data["hooks"]["PostToolUse"][0] == original["hooks"]["PostToolUse"][0]


@pytest.mark.parametrize(
    "stale_command",
    [
        "rtk-claude-safe subagent-depth-hook",
        "rtk-claude-safe.exe subagent-depth-hook",
        r"C:\Tools\rtk-claude-safe.exe subagent-depth-hook",
        "python -m rtk_claude_safe subagent-depth-hook",
        "python3 -m rtk_claude_safe subagent-depth-hook",
        "python3.11 -m rtk_claude_safe subagent-depth-hook",
        r"C:\Python312\python.exe -m rtk_claude_safe subagent-depth-hook",
        '"/opt/Python Dir/python3" -m rtk_claude_safe subagent-depth-hook',
        '"/opt/Safe Dir/rtk-claude-safe" subagent-depth-hook',
    ],
)
def test_subagent_depth_hooks_remove_exact_managed_variants(tmp_path, stale_command: str) -> None:
    hooks_path = tmp_path / ".codex" / "hooks.json"
    hooks_path.parent.mkdir()
    hooks_path.write_text(
        json.dumps(
            {
                "hooks": {
                    "PreToolUse": [
                        {
                            "matcher": CODEX_AGENT_MATCHER,
                            "hooks": [
                                {"type": "command", "command": stale_command},
                                {
                                    "type": "command",
                                    "command": "echo rtk-claude-safe subagent-depth-hook",
                                },
                            ],
                        }
                    ]
                }
            }
        ),
        encoding="utf-8",
    )

    assert patch_subagent_depth_hooks(hooks_path, command="/new/rtk-claude-safe subagent-depth-hook")

    commands = _commands(hooks_path)
    assert stale_command not in commands
    assert "echo rtk-claude-safe subagent-depth-hook" in commands
    assert "/new/rtk-claude-safe subagent-depth-hook" in commands


def test_remove_subagent_depth_hooks_preserves_unrelated_entries(tmp_path) -> None:
    hooks_path = tmp_path / ".codex" / "hooks.json"
    hooks_path.parent.mkdir()
    assert patch_subagent_depth_hooks(hooks_path, command="/bin/rtk-claude-safe subagent-depth-hook")
    data = json.loads(hooks_path.read_text(encoding="utf-8"))
    data["hooks"]["PreToolUse"].insert(
        0,
        {"matcher": "^Bash$", "hooks": [{"type": "command", "command": "echo keep"}]},
    )
    hooks_path.write_text(json.dumps(data), encoding="utf-8")

    assert remove_subagent_depth_hooks(hooks_path)

    data = json.loads(hooks_path.read_text(encoding="utf-8"))
    assert data["hooks"] == {
        "PreToolUse": [
            {"matcher": "^Bash$", "hooks": [{"type": "command", "command": "echo keep"}]}
        ]
    }

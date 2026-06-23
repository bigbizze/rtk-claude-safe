from __future__ import annotations

import pytest

from rtk_claude_safe.allowlist import (
    is_already_rtk_wrapped,
    is_complex_shell_command,
    matches_allowlist,
    should_wrap_command,
)


@pytest.mark.parametrize(
    "command",
    [
        "git status",
        "git status --short",
        "git log --oneline",
        "npm run test",
        "pnpm build",
        "cargo test --workspace",
        "gh pr list",
    ],
)
def test_matches_allowlist(command: str) -> None:
    assert matches_allowlist(command)


@pytest.mark.parametrize(
    "command",
    [
        "ls",
        "curl https://example.com",
        "git diff",
        "gh pr view 123 --comments",
        "gh pr status",
        "npx cowsay",
        "playwright test",
    ],
)
def test_does_not_match_allowlist(command: str) -> None:
    assert not matches_allowlist(command)


@pytest.mark.parametrize(
    "command",
    [
        "git status | cat",
        "git status && git diff --stat",
        "cd app && npm test",
        "FOO=bar npm test",
        "echo $(git status)",
        "git status > out.txt",
        "git status & curl https://example.com",
    ],
)
def test_complex_shell_commands_are_not_wrapped(command: str) -> None:
    assert is_complex_shell_command(command)
    assert not should_wrap_command(command)


@pytest.mark.parametrize(
    "command",
    [
        "rtk git status",
        "rtk proxy git diff",
        "rtk --whatever git status",
        "/usr/local/bin/rtk git status",
    ],
)
def test_already_rtk_wrapped(command: str) -> None:
    assert is_already_rtk_wrapped(command)
    assert not should_wrap_command(command)


@pytest.mark.parametrize(
    "command",
    [
        "env curl https://example.com",
        "env FOO=bar npm test",
        'git status "unterminated',
    ],
)
def test_uncertain_or_nested_commands_are_not_wrapped(command: str) -> None:
    assert not should_wrap_command(command)

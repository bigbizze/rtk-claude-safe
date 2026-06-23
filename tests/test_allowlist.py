from __future__ import annotations

import pytest

from rtk_claude_safe.allowlist import (
    is_already_rtk_wrapped,
    is_complex_shell_command,
    matches_allowlist,
    rewrite_command_for_agent,
    should_wrap_command,
)


@pytest.mark.parametrize(
    "command",
    [
        "git status",
        "git status --short",
        "git status -s",
        "git log --oneline -n 20",
        "git log --oneline --max-count 20",
        "npm run test",
        "npm run typecheck",
        "pnpm run format:check",
        "pnpm exec vitest run",
        "pnpm build",
        "cargo test --workspace",
        "gh pr list",
        "gh pr view 123",
        "gh issue view 456",
        "pip list",
        "pip outdated",
        "pip show flask",
        "env",
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
        "gh pr status",
        "npx cowsay",
        "playwright test",
        "env curl https://example.com",
        "bare-command-that-does-not-exist",
    ],
)
def test_does_not_match_allowlist(command: str) -> None:
    assert not matches_allowlist(command)


@pytest.mark.parametrize(
    ("command", "expected"),
    [
        ("git status", "rtk git status"),
        ("git log --oneline -n 20", "rtk git log --oneline -n 20"),
        ("npm run build:ci", "rtk npm run build:ci"),
        ("pnpm exec prettier --check .", "rtk pnpm exec prettier --check ."),
        ("gh pr view 123 --comments", "rtk gh pr view 123 --comments"),
        ("pip show flask", "rtk pip show flask"),
        ("eslint .", "rtk lint ."),
        ("npx vitest run", "rtk npx vitest run"),
    ],
)
def test_rewrite_command_for_agent(command: str, expected: str) -> None:
    assert rewrite_command_for_agent(command, "codex") == expected
    assert rewrite_command_for_agent(command, "claude") == expected


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


@pytest.mark.parametrize(
    "command",
    [
        "vitest --watch",
        "tsc --watch",
        "npm run dev",
        "npm run start",
        "pnpm run storybook",
        "pnpm run test:watch",
        "gh pr view 123 --json title",
        "gh pr view 123 --json=title",
        "gh pr view 123 --jq .title",
        "gh pr view 123 --jq=.title",
        "gh pr view 123 --template '{{.title}}'",
        "gh issue view 123 --template='{{.title}}'",
        "pnpm list --json=true",
        "git status --porcelain",
        "git diff --name-only",
        "git diff --stat --name-only",
        "git diff --raw",
        "git log --oneline",
        "git log --oneline -n 100",
        "git log --oneline --format=%H",
        "git log --oneline --stat -n 10",
        "git commit -m test",
        "git push",
        "git stash push",
        "git worktree add ../other",
        "dotnet test",
        "cargo build --message-format=json",
        "cargo test --message-format json",
        "vitest run --coverage",
        "vitest run --reporter=json",
        "vitest run --reporter json",
        "vitest run --outputFile report.json",
        "eslint . -f json",
        "ruff check . --output-format json",
        "ruff check . --output-format=github",
        "go test -json ./...",
        "pytest --json-report",
        "pytest --junitxml=report.xml",
        "mypy --junit-xml report.xml",
        "pnpm exec vitest --watch",
        "pnpm exec prisma migrate dev",
        "biome check",
    ],
)
def test_risky_subsets_are_not_wrapped(command: str) -> None:
    assert rewrite_command_for_agent(command, "codex") is None
    assert not should_wrap_command(command)

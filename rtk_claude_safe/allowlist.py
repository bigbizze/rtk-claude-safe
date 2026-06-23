"""Shared safe command allowlist for Claude and Codex adapters."""

from __future__ import annotations

import fnmatch
import re
import shlex
from pathlib import Path

# Order is preserved so generated settings remain human-readable.
SCOPED_PATTERNS: list[str] = [
    # cargo
    "cargo test*",
    "cargo build*",
    "cargo clippy*",
    "cargo check*",
    "cargo run*",
    "cargo fmt --all --check*",
    "cargo fmt --check*",
    "cargo doc*",
    "cargo install*",
    "cargo nextest*",
    # generic test/lint/typecheck runners
    "vitest*",
    "jest*",
    "pytest*",
    "go test*",
    "dotnet test*",
    "tsc*",
    "eslint*",
    "ruff check*",
    "ruff format --check*",
    "mypy*",
    "prettier --check*",
    "next build*",
    "biome check*",
    "biome lint*",
    # pnpm / npm
    "pnpm install*",
    "pnpm run *",
    "pnpm test*",
    "pnpm lint*",
    "pnpm build*",
    "pnpm list*",
    "pnpm outdated*",
    "pnpm exec *",
    "npm install*",
    "npm run *",
    # npx
    "npx tsc*",
    "npx eslint*",
    "npx prisma*",
    "npx biome*",
    "npx prettier*",
    "npx vitest*",
    "npx playwright codegen*",
    # prisma
    "prisma generate*",
    "prisma migrate*",
    "prisma db push*",
    # git
    "git status*",
    "git log*",
    "git add*",
    "git commit*",
    "git push*",
    "git pull*",
    "git fetch*",
    "git stash*",
    "git branch*",
    "git checkout*",
    "git switch*",
    "git merge*",
    "git rebase*",
    "git cherry-pick*",
    "git show *",
    "git worktree*",
    "git diff --name-only*",
    "git diff --stat*",
    # gh
    "gh pr list*",
    "gh issue list*",
    "gh run list*",
    "gh run view*",
    "gh workflow list*",
    "gh workflow view*",
    "gh repo view*",
    "gh repo list*",
    # misc
    "tree*",
    "wc*",
    "env",
]

_COMPLEX_SHELL_TOKENS = ("|", ">", "<", ";", "&", "&&", "||", "\n", "`", "$(", "<(", ">(")
_ENV_PREFIX_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")


def is_complex_shell_command(command: str) -> bool:
    """Return True when a command contains syntax we should not rewrite."""
    stripped = command.strip()
    if not stripped:
        return False
    if _ENV_PREFIX_RE.match(stripped):
        return True
    return any(token in stripped for token in _COMPLEX_SHELL_TOKENS)


def is_already_rtk_wrapped(command: str) -> bool:
    """Return True when the command already starts with the rtk executable."""
    parts = _split_command(command)
    if not parts:
        return False
    executable = Path(parts[0]).name.lower()
    return executable in {"rtk", "rtk.exe"}


def _split_command(command: str) -> list[str] | None:
    try:
        return shlex.split(command.strip())
    except ValueError:
        return None


def matches_allowlist(command: str) -> bool:
    """Return True when the raw command matches one of the safe command patterns."""
    stripped = command.strip()
    if not stripped:
        return False
    return any(fnmatch.fnmatchcase(stripped, pattern) for pattern in SCOPED_PATTERNS)


def should_wrap_command(command: str) -> bool:
    """Return True when a Codex Bash command should be rewritten through rtk."""
    parts = _split_command(command)
    if not parts:
        return False
    if Path(parts[0]).name.lower() == "env" and len(parts) > 1:
        return False
    return (
        not is_already_rtk_wrapped(command)
        and not is_complex_shell_command(command)
        and matches_allowlist(command)
    )


def build_claude_scoped_hooks(command: str = "rtk hook claude") -> list[dict]:
    """Build Claude Code scoped hook entries from the shared allowlist."""
    return [
        {"type": "command", "command": command, "if": f"Bash({pattern})"}
        for pattern in SCOPED_PATTERNS
    ]

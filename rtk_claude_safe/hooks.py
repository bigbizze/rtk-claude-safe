"""The scoped list of `Bash(<pattern>*)` matchers that should run rtk's hook.

These replace rtk's default catch-all `Bash` hook so that rtk only intercepts commands whose
output benefits from token-killing (test runners, linters, package managers, git, gh, etc).
"""

from __future__ import annotations

# Order is preserved so the resulting settings.json is human-readable.
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
    "env*",
]


def build_scoped_hooks(command: str = "rtk hook claude") -> list[dict]:
    """Build the list of scoped hook entries that should sit under matcher='Bash'."""
    return [
        {"type": "command", "command": command, "if": f"Bash({pattern})"}
        for pattern in SCOPED_PATTERNS
    ]

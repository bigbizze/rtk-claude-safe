j# rtk-claude-safe

A Python wrapper around [rtk (Rust Token Killer)](https://github.com/rtk-ai/rtk) that installs the
right binary for your platform and applies a curated, **safer** Claude Code hook configuration in
one command.

```bash
pipx install git+https://github.com/bigbizze/rtk-claude-safe.git
rtk-claude-safe init
```

## Why this exists

`rtk init -g --hook-only` installs a `PreToolUse` hook with `matcher: "Bash"` and no `if` clause —
i.e. **rtk rewrites every single Bash command Claude runs**. That sounds fine until you actually
read the rtk issue tracker, and it isn't. There are several distinct classes of "the agent
confidently acts on wrong output" bugs that are live in current rtk releases and won't be fully
fixed for a while. The default catch-all hook puts every one of them on the critical path.

The fix isn't to stop using rtk — its filtering really does save 60-90% of tokens on noisy
commands. The fix is to scope the hook to the commands where the filter is well-trodden and
correctness-preserving, and to leave everything else alone.

That's all this package does: install rtk, run rtk's official hook installer, then rewrite
`~/.claude/settings.json` so the bare `{ "command": "rtk hook claude" }` entry under the `Bash`
matcher is replaced with ~70 narrowly-scoped `Bash(<pattern>*)` matchers.

### The specific failure modes that drove the allowlist

These are all open bugs in `rtk-ai/rtk` as of late April 2026:

- **`ls` is broken in many environments.** Empty output on macOS, on non-English locales, when
  `ls` is aliased to `eza`/`exa`/`lsd`, and with `-1`. (Issues #1418, #1475, #1448, #1342, #1321,
  #1276, #803.) **Hard-excluded.**
- **Piped output gets silently corrupted.** `rtk grep`, `rtk find`, `rtk ls`, and `git diff
  --stat` produce wrong results when stdout is not a TTY because there's no `isatty` passthrough
  yet. (Issues #1282, #838, #1486.) `grep` and `find` are excluded; `git diff --stat` is included
  only because in practice Claude rarely pipes it.
- **`curl` destroys JSON.** Issues #1152 (JSON value destruction) and #1015 (unquoted JSON keys)
  mean `curl` output gets returned as syntactically broken JSON. The rtk README itself
  recommends `[hooks] exclude_commands = ["curl"]`. **Hard-excluded.**
- **`gh ... --comments` returns no comments.** `gh pr view --comments` and `gh issue view
  --comments` use a hardcoded `--json` field list that omits comments entirely, so the agent
  gets back `[]` and confidently reports "no comments" when there are dozens. (Issue #720.)
  **The allowlist deliberately doesn't include `--comments` variants.**
- **`gh pr status` is broken outright.** Fails with `Unknown JSON field: "currentBranch"`.
  (Issue #960.) **Excluded.**
- **`npx <unknown-package>` fails with ENOENT.** rtk converts unknown `npx <pkg>` to
  `npm run <pkg>`. (Issue #1080.) The allowlist only includes specific known-good prefixes
  (`npx tsc`, `npx eslint`, `npx prisma`, `npx biome`, `npx prettier`, `npx vitest`,
  `npx playwright codegen`).
- **`git diff` for code review is lossy by design.** rtk's diff condenser drops content that
  matters for review. (#1313 truncation class, #1486 piped corruption.) The allowlist scopes
  `git diff` to `--name-only` and `--stat` only; bare `git diff` is **not** wrapped, so use
  `rtk proxy git diff` or raw `git diff` for review.
- **`playwright test` strips DOM/locator/call-log on failure.** (Issue #690 — the rtk README
  also recommends excluding it.) **Not in the allowlist.**
- **`vitest run --coverage` silently drops coverage data.** (Issue #220.) Bare `vitest*` is
  whitelisted, but coverage runs should go through `rtk proxy vitest run --coverage`.

The allowlist itself — defined in `rtk_claude_safe/hooks.py` — covers the commands where the rtk
filter is actively dogfooded by the maintainer or has been stable across several minor releases:
cargo (build/test/clippy/check/run/fmt --check/doc/install/nextest), the JS/TS test+lint+type
toolchain (vitest, jest, pytest, tsc, eslint, biome, ruff, mypy, prettier --check, next build),
pnpm/npm package management and runners, prisma generate/migrate/db push, the safe surface of git
(status, log, add, commit, push/pull/fetch, stash, branch, checkout, switch, merge, rebase,
cherry-pick, show, worktree, diff --name-only, diff --stat), the safe surface of gh (list/view
subcommands, no `--comments`, no `pr status`), and a few small utilities (`tree`, `wc`, `env`).

### What `rtk-claude-safe init` does

1. **Install rtk if missing.** Detects OS/arch (mirroring rtk's `install.sh`), fetches the latest
   release from `rtk-ai/rtk`, and extracts the binary to `~/.local/bin/rtk`. Skipped if `rtk` is
   already on `PATH`.
2. **Run `rtk init -g --hook-only`.** Lays down rtk's normal global hook + config.
3. **Patch `~/.claude/settings.json`.** Finds the `PreToolUse` entry whose matcher is `Bash`,
   removes the bare `rtk hook claude` hook, and replaces it with the scoped list. Idempotent —
   running again is a no-op if the scoped hooks are already in place. Other hooks under the same
   matcher are preserved.

### Recommended companions (not done by this tool)

These complement the scoped allowlist but live outside `~/.claude/settings.json`:

- **Pin to rtk ≥ 0.37.1.** Several of the bugs cited above were fixed in the v0.34–v0.37 window
  (`read`-as-`cat`, diff condense, git subdir argument parsing, the new Rust hook engine).
- **Add a pipe-detection bypass at the top of `~/.claude/hooks/rtk-rewrite.sh`.** Suggested in
  #1282:
  ```bash
  if [[ "$CMD" =~ [\>\|\`] ]] || [[ "$CMD" == *'$('* ]] || [[ "$CMD" == *'<('* ]]; then
    exit 0
  fi
  ```
  This neutralizes the whole piped-corruption class without waiting for upstream.
- **Belt-and-braces `~/.config/rtk/config.toml`:**
  ```toml
  [hooks]
  exclude_commands = [
    "ls", "curl", "playwright", "rtk json",
    "next dev", "next start", "prisma studio", "tsc --watch",
  ]
  ```
  These catch the cases where Claude reaches for the bare command name even if your `if`-gated
  allowlist wouldn't have matched it.

## Layout

```
rtk_claude_safe/
├── cli.py         # argparse entry point
├── installer.py   # OS/arch detection, GitHub release download, binary extraction
├── hooks.py       # the curated list of Bash(<pattern>*) matchers
└── settings.py    # idempotent settings.json patcher
```

`hooks.py` is the single place to edit if you want to tighten or loosen the allowlist for your
stack.

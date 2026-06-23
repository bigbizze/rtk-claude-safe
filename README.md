# rtk-claude-safe

A Python wrapper around [rtk (Rust Token Killer)](https://github.com/rtk-ai/rtk) that installs the
right binary for your platform and applies curated, **safer** Claude Code and Codex hook
configuration in one command.

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

That's all this package does: install rtk, detect supported global agent config folders, and patch
only the agents that are already present. For Claude Code, it runs rtk's official hook installer
and rewrites `~/.claude/settings.json` so the bare `{ "command": "rtk hook claude" }` entry under
the `Bash` matcher is replaced with scoped `Bash(<pattern>*)` candidate matchers that call
`rtk-claude-safe claude-hook`. For Codex, it installs one `^Bash$` `PreToolUse` hook in
`~/.codex/hooks.json`. Both hook executables parse `tool_input.command`, apply the same fail-open
safety classifier, and emit direct `updatedInput.command` rewrites only for known safe command
shapes.

### The specific failure modes that drove the allowlist

These are representative upstream bug classes that shape the default policy:

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
- **Machine-readable command output must stay raw.** JSON, jq/template output, git porcelain,
  null-delimited output, raw diffs, and name-only/name-status data are intended for another
  program. The classifier rejects those shapes before any allowlist rule can match.
- **`gh pr status` is broken outright.** Fails with `Unknown JSON field: "currentBranch"`.
  (Issue #960.) **Excluded.**
- **`npx <unknown-package>` has historically been fragile.** The classifier only includes
  specific known-good `npx` tools and preserves the `npx` invocation by rewriting to
  `rtk npx ...`.
- **`git diff` for code review is lossy by design.** rtk's diff condenser drops content that
  matters for review. (#1313 truncation class, #1486 piped corruption.) The classifier allows
  only `git diff --stat` and rejects `--name-only`, bare `git diff`, and machine-readable diff
  forms.
- **`playwright test` strips DOM/locator/call-log on failure.** (Issue #690 — the rtk README
  also recommends excluding it.) **Not in the allowlist.**
- **Watch/dev/server commands should not be captured.** Long-running commands can buffer or
  suppress useful output through filtered routes. The classifier rejects watch flags and package
  scripts such as `dev`, `start`, `serve`, `server`, `preview`, `storybook`, and `watch`.

The rewrite policy itself — defined in `rtk_claude_safe/allowlist.py` — is a parsed-command
classifier, not a raw wildcard list. It covers safe cargo/test/lint/typecheck/build commands,
named npm/pnpm scripts, selected `npx` tools, prisma generate/migrate/db push, tightly-scoped git
orientation commands, safe gh list/view commands, read-only pip inventory commands, and a few small
utilities (`tree`, `wc`, `env`).

### What `rtk-claude-safe init` does

1. **Detect installed agents.** If `~/.claude/` exists, Claude Code is patched. If `~/.codex/`
   exists, Codex is patched. If neither exists, the command exits without installing rtk or creating
   agent config directories.
2. **Install rtk if needed.** Detects OS/arch (mirroring rtk's `install.sh`), fetches the latest
   release from `rtk-ai/rtk`, and extracts the binary to `~/.local/bin/rtk`. Skipped if `rtk` is
   already on `PATH`.
3. **Patch Claude Code when present.** Runs `rtk init -g --hook-only`, finds every `PreToolUse`
   entry whose matcher is `Bash`, removes existing RTK hooks, and adds the current scoped candidate
   list once. Those scoped hooks call `rtk-claude-safe claude-hook`, which rejects complex or
   uncertain shell commands and emits direct `updatedInput.command` rewrites. Idempotent — running
   again is a no-op if the scoped hooks are already current. Other user hooks under the same matcher
   are preserved.
4. **Patch Codex when present.** Creates or updates `~/.codex/hooks.json` with one `^Bash$`
   `PreToolUse` command hook that calls `rtk-claude-safe codex-hook`. Other hook events, matcher
   groups, and user hooks are preserved.

### Codex support

Codex support is experimental and scoped to the interactive Codex CLI on macOS, Linux, and WSL. It
does not use or require upstream `rtk hook codex`; this package's own hook rewrites allowlisted
simple Bash commands directly to the classifier's RTK command.

After `rtk-claude-safe init` patches Codex, open Codex CLI, run `/hooks`, review and trust the
`rtk-claude-safe` hook, then ask Codex to run `git status`. The expected rewritten command is
`rtk git status`.

Not supported in this release:

- native Windows Codex command execution
- `codex exec`
- TOML editing
- `PermissionRequest` behavior
- using this as a security boundary

Codex matchers apply to tool names, not shell command strings. That means Codex gets one `^Bash$`
hook, and the Python hook executable applies the allowlist internally. The hook fails open: invalid
payloads, non-Bash tools, complex shell commands, excluded commands, and already-wrapped `rtk ...`
commands emit no output so Codex runs the original command.

### Recommended companions (not done by this tool)

These complement the scoped allowlist but live outside `~/.claude/settings.json`:

- **Pin to rtk ≥ 0.37.1.** Several of the bugs cited above were fixed in the v0.34–v0.37 window
  (`read`-as-`cat`, diff condense, git subdir argument parsing, the new Rust hook engine).
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
├── allowlist.py        # shared safe command classifier and rewrite mapper
├── claude_hook.py      # Claude stdin/stdout PreToolUse hook handler
├── claude_settings.py  # idempotent Claude settings.json patcher
├── cli.py             # argparse entry point
├── codex_hook.py      # Codex stdin/stdout PreToolUse hook handler
├── codex_settings.py  # idempotent Codex hooks.json patcher
├── hooks.py           # compatibility shim
├── installer.py       # OS/arch detection, GitHub release download, binary extraction
└── settings.py        # compatibility shim
```

`allowlist.py` is the single place to edit if you want to tighten or loosen the rewrite policy for
your stack.

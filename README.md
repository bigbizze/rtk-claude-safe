# rtk-claude-safe

A Python wrapper around [rtk (Rust Token Killer)](https://github.com/rtk-ai/rtk) that installs the
right binary for your platform and applies curated, **safer** Claude Code and Codex hook
configuration in one command.

```bash
pipx install git+https://github.com/bigbizze/rtk-claude-safe.git
rtk-claude-safe init
```

## Why this exists

RTK's broad built-in hook integration can put every Bash command on a rewrite path. That sounds
fine until you actually read the RTK issue tracker, and it is not. There are several distinct
classes of "the agent confidently acts on wrong output" bugs that are live in current RTK releases.
A catch-all hook puts every one of them on the critical path.

The fix isn't to stop using rtk — its filtering really does save 60-90% of tokens on noisy
commands. The fix is to scope the hook to the commands where the filter is well-trodden and
correctness-preserving, and to leave everything else alone.

That's all this package does: install or validate a supported stable RTK binary, detect supported
global agent config folders, and patch only the agents that are already present. For Claude Code,
it writes scoped `Bash(<pattern>*)` candidate matchers in `~/.claude/settings.json` that call
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
named npm/pnpm scripts, selected `npx` tools, selected Prisma commands (`generate`, `db push`, and
`migrate dev`), tightly-scoped git orientation commands, safe gh list/view commands except
comment-fetching modes, read-only pip inventory commands, and a few small utilities (`tree`, `wc`,
`env`).

### What `rtk-claude-safe init` does

1. **Detect installed agents.** If `~/.claude/` exists, Claude Code is patched. If `~/.codex/`
   exists, Codex is patched. If neither exists, the command exits without installing rtk or creating
   agent config directories.
2. **Validate or install RTK.** If `rtk` already exists on `PATH`, it must be a supported stable
   version. This release is updated for RTK `v0.42.4` and requires `rtk >= 0.42.4`; older,
   prerelease, or unparseable binaries make `init` fail before any hook config is modified. If RTK
   is missing, the installer downloads the latest stable GitHub release, verifies release metadata,
   API digests, checksums, and archive contents, then atomically installs `rtk` to
   `~/.local/bin/rtk`. The downloaded binary must be reachable on `PATH` before config is patched.
3. **Patch Claude Code when present.** Finds or creates the global `PreToolUse` matcher groups,
   removes older RTK-managed hooks, and adds the current scoped candidate list once. Those scoped
   hooks call `rtk-claude-safe claude-hook`, which rejects complex or uncertain shell commands and
   emits direct `updatedInput.command` rewrites. Idempotent — running again is a no-op if the scoped
   hooks are already current. Other user hooks under the same matcher are preserved.
4. **Patch Codex when present.** Creates or updates `~/.codex/hooks.json` with one `^Bash$`
   `PreToolUse` command hook that calls `rtk-claude-safe codex-hook`. Other hook events, matcher
   groups, and user hooks are preserved.

If any config write fails, the command rolls back the files it touched. Hidden hook commands take
the opposite stance: if RTK is missing, stale, prerelease, or unparseable at hook runtime, they emit
no output so Claude or Codex runs the original command unchanged.

### Codex support

Codex support is experimental and scoped to the interactive Codex CLI on macOS, Linux, and WSL. It
does not use or require upstream `rtk hook codex`; this package's own hook rewrites allowlisted
simple Bash commands directly to the classifier's RTK command.

After `rtk-claude-safe init` patches Codex, open Codex CLI, run `/hooks`, review and trust the
`rtk-claude-safe` hook, then ask Codex to run `git status`. The expected rewritten command is
`rtk git status`.

Not supported in this release:

- native Windows Codex command execution
- Windows ARM64 RTK installation
- `codex exec`
- TOML editing
- `PermissionRequest` behavior
- using this as a security boundary

Codex matchers apply to tool names, not shell command strings. That means Codex gets one `^Bash$`
hook, and the Python hook executable applies the allowlist internally. The hook fails open: invalid
payloads, non-Bash tools, complex shell commands, excluded commands, and already-wrapped `rtk ...`
commands emit no output so Codex runs the original command.

### Recommended Companion Config

The installer enforces `rtk >= 0.42.4` for mutations, but RTK's own config can still provide a
backup exclusion layer outside this package:

```toml
[hooks]
exclude_commands = [
  "ls", "curl", "playwright", "rtk json",
  "next dev", "next start", "prisma studio", "tsc --watch",
]
```

This is optional. The Python hooks already deny those shapes before rewriting, but the RTK config
helps if another agent integration uses RTK directly.

## Layout

```
rtk_claude_safe/
├── allowlist.py        # shared safe command classifier and rewrite mapper
├── claude_hook.py      # Claude stdin/stdout PreToolUse hook handler
├── claude_settings.py  # idempotent Claude settings.json patcher
├── cli.py             # argparse entry point
├── codex_hook.py      # Codex stdin/stdout PreToolUse hook handler
├── codex_settings.py  # idempotent Codex hooks.json patcher
├── hook_command.py    # stable hook command path construction
├── hooks.py           # compatibility shim
├── installer.py       # OS/arch detection, GitHub release download, binary extraction
├── managed_hooks.py   # exact detection of old/current managed hook commands
├── rtk_runtime.py     # RTK version probing and compatibility checks
└── settings.py        # compatibility shim
```

`allowlist.py` is the single place to edit if you want to tighten or loosen the rewrite policy for
your stack.

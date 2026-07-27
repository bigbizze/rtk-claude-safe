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
  only narrow orientation checks such as `git diff --stat` and `git diff --check`, and rejects
  pathspecs, revisions, `--name-only`, bare `git diff`, and machine-readable diff forms.
- **`playwright test` strips DOM/locator/call-log on failure.** (Issue #690 — the rtk README
  also recommends excluding it.) **Not in the allowlist.**
- **Watch/dev/server commands should not be captured.** Long-running commands can buffer or
  suppress useful output through filtered routes. The classifier rejects watch flags and package
  scripts such as `dev`, `start`, `serve`, `server`, `preview`, `storybook`, and `watch`.

The rewrite policy itself — defined in `rtk_claude_safe/allowlist.py` — is a parsed-command
classifier, not a raw wildcard list. It covers safe cargo/test/lint/typecheck/build commands,
named npm/pnpm scripts, exact pnpm validation shorthands and package-filtered pnpm validation,
selected `npx` tools, selected Prisma commands (`generate`, `db push`, and `migrate dev`),
tightly-scoped git orientation commands, safe gh list/view commands except comment-fetching modes,
read-only pip inventory commands, and a few small utilities (`tree`, `wc`, `env`).

### Curated environment prefixes

One or more contiguous leading `NAME=value` assignments may precede an otherwise allowlisted
command. Names and values are case-sensitive, and every assignment must match the command's
detected stack:

| Scope | Exact accepted assignments |
| --- | --- |
| Any allowlisted command | `LC_ALL=C` or `LC_ALL=C.UTF-8`; `LANG=C` or `LANG=C.UTF-8`; `NO_COLOR=1` or `NO_COLOR=true`; `FORCE_COLOR=0` |
| Rust, Go, Python, or Node.js | `CI=1` or `CI=true`; `TZ=UTC` |
| Rust/Cargo | `CARGO_TERM_COLOR=never`; `RUST_BACKTRACE=0`, `RUST_BACKTRACE=1`, or `RUST_BACKTRACE=full` |
| Go | `CGO_ENABLED=0` or `CGO_ENABLED=1`; `GOMAXPROCS` as an ASCII decimal integer from 1 through 256; `GOTOOLCHAIN=local`; `GOWORK=auto` or `GOWORK=off` |
| Python | `PYTHONUNBUFFERED=1`; `PYTHONDONTWRITEBYTECODE=1`; `PYTHONHASHSEED=random` or an ASCII decimal integer from 0 through 4294967295 |
| Pytest only | `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1` |
| Node.js | `NODE_ENV=development`, `NODE_ENV=production`, or `NODE_ENV=test`; `NODE_NO_WARNINGS=1`; `NODE_DISABLE_COLORS=1`; each of `npm_config_color`, `npm_config_progress`, `npm_config_audit`, `npm_config_fund`, and `npm_config_update_notifier` set to `false` or `0` |

Validated assignments remain before RTK and are safely re-rendered rather than copied as raw shell
text:

```text
CARGO_TERM_COLOR=never cargo test
→ CARGO_TERM_COLOR=never rtk cargo test

LANG='C.UTF-8' PYTHONHASHSEED=random pytest tests
→ LANG=C.UTF-8 PYTHONHASHSEED=random rtk pytest tests

LC_ALL=C git status && NODE_ENV=test npm run test
→ LC_ALL=C rtk git status && NODE_ENV=test rtk npm run test
```

The complete prefix is rejected if a name is unknown, repeated, has the wrong value, or belongs to
the wrong stack. Execution-routing and option-injection variables remain denied, including `PATH`,
loader variables, `NODE_OPTIONS`, `PYTHONPATH`, `PYTEST_ADDOPTS`, `GOFLAGS`, `GODEBUG`,
`RUSTFLAGS`, `RUSTC_WRAPPER`, `CARGO_HOME`, and every `RTK_*` name. The `env NAME=value command`,
`export`, and `sudo` forms are not supported. Pipes, redirects, multiline scripts, substitutions,
and other unsupported shell forms also continue to fail open to the original command.

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
   hooks call `rtk-claude-safe claude-hook`, which rejects unsupported or uncertain shell syntax and
   emits direct `updatedInput.command` rewrites. Candidate patterns cover allowlisted command names,
   curated first-variable names, and chained positions; the runtime classifier remains
   authoritative. Top-level `&&`, `||`, and `;` chains are classified segment by segment, so a
   command such as `cd app && npm run test` becomes `cd app && rtk npm run test`. Idempotent —
   running again is a no-op if the scoped hooks are already current. Other user hooks under the
   same matcher are preserved.
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
payloads, non-Bash tools, unsupported shell syntax, excluded commands, and already-wrapped
`rtk ...` commands — including assignment-prefixed RTK commands — emit no output so Codex runs the
original command.
Top-level `&&`, `||`, and `;` shell lists are supported when at least one segment is allowlisted
and every other segment is an explicitly neutral preserved command such as `cd app`; unsupported
shell syntax such as pipes, redirects, backgrounding, grouping, and substitutions still fails open.
Codex also has a named narrow-exception contract for commands that are acceptable to auto-allow as
part of a rewritten shell list even though they are not RTK-wrapped themselves. Current exceptions
include `gofmt -w <explicit .go files> && go test ...`, which rewrites only the test segment, for
example `gofmt -w main.go git.go && rtk go test ./...`; and `cargo fmt` or `cargo fmt --all`
immediately before `cargo test`, `cargo check`, or `cargo clippy`, which rewrites only the Cargo
validation segment.

### Codex SQLite Log Maintenance

`init` does not modify Codex's SQLite log database. The log repair commands are explicit because
they mutate `~/.codex/logs_2.sqlite` and should only run after all Codex CLI sessions are closed.
Each command asks the user to close Codex, requires an exact `Y` confirmation, checks the process
list for native Codex and Node wrapper processes, and checks again immediately before writing.

The default database path is resolved from `--database`, then `CODEX_SQLITE_HOME`, then
`CODEX_HOME`, then `~/.codex/logs_2.sqlite`. `CODEX_SQLITE_HOME` may point either at the database
file or at the directory containing `logs_2.sqlite`. The commands fail if the database does not
already exist; they do not create a replacement database. Native Windows Codex SQLite maintenance
is not supported.

Available maintenance commands:

- `rtk-claude-safe repair-codex-sqlite` installs a managed `BEFORE INSERT` trigger named
  `codex_ignore_low_level_logs` that ignores `TRACE`, `DEBUG`, and `INFO` rows before SQLite writes
  them. Re-running the command is idempotent when the exact managed trigger is already present. A
  same-name trigger with different SQL is treated as a conflict and is left untouched.
- `rtk-claude-safe revert-codex-sqlite-repair` removes only the exact managed trigger. If the
  trigger is absent, the command succeeds without changing the database. A same-name trigger with
  different SQL is left untouched.
- `rtk-claude-safe vacuum-codex-sqlite` runs `PRAGMA wal_checkpoint(TRUNCATE)`, `VACUUM`, and
  `PRAGMA optimize`, then reports database and WAL sizes before and after. It does not require the
  repair trigger to be installed. Add `--backup` to write an adjacent timestamped SQLite backup
  before maintenance; backups are optional and are not deleted if a later maintenance step fails.

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
├── codex_sqlite.py    # guarded Codex logs_2.sqlite repair, revert, and vacuum commands
├── hook_command.py    # stable hook command path construction
├── hooks.py           # compatibility shim
├── installer.py       # OS/arch detection, GitHub release download, binary extraction
├── managed_hooks.py   # exact detection of old/current managed hook commands
├── rtk_runtime.py     # RTK version probing and compatibility checks
└── settings.py        # compatibility shim
```

`allowlist.py` is the single place to edit if you want to tighten or loosen the rewrite policy for
your stack.

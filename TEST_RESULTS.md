# v0.4.0 Verification Results

Recorded: 2026-06-23

Branch: `update-rtk-0424-baseline`

## Scope

This change updates the repository's RTK compatibility baseline to stable RTK `v0.42.4` and adds
runtime enforcement:

- `rtk-claude-safe init` fails before config mutation when an existing RTK binary is older than
  `0.42.4`, prerelease, or unparseable.
- Missing RTK still installs from the latest stable GitHub release, but the installer verifies
  release metadata, API digests, checksums, archive shape, binary version, and PATH reachability.
- Hidden Claude and Codex hook entrypoints fail open when runtime RTK is missing or unsupported.
- Claude settings are patched directly by this package; the installer no longer delegates to an
  upstream broad hook installer.

## Automated Checks

- `uv run --extra dev pytest -q`
  - Result: passed
  - Coverage: 233 tests
- `python3 -m compileall rtk_claude_safe`
  - Result: passed
- `git diff --check master...HEAD`
  - Result: passed
- `rtk-claude-safe --version`
  - Result: `rtk-claude-safe 0.4.0`
- `rtk --version`
  - Result after upgrade: `rtk 0.42.4`

## Review Loop

- Pass 1 finding addressed:
  - Windows hook command cleanup parsed stored commands as POSIX shell, so unquoted Windows
    backslash paths emitted by `subprocess.list2cmdline()` were not recognized as managed hooks.
  - Commit: `9f09176 Recognize Windows managed hook commands`
- Pass 2 finding addressed:
  - Installer release verification needed direct tests for asset/digest/checksum wiring.
  - Commit: `440330f Cover verified RTK release downloads`
- Pass 3 findings addressed:
  - Added archive payload digest mismatch coverage after API and `checksums.txt` agree.
  - Added subprocess-backed runtime policy tests for stale, prerelease, and unparseable RTK output.
  - Commit: `55b2142 Harden RTK verification tests`
- Pass 4 result:
  - No findings worth addressing.

## Global Install And Idempotency

- Initial installed RTK before upgrade:
  - `rtk 0.37.2`
- Stale RTK behavior:
  - `rtk-claude-safe init` exited `1`.
  - `~/.claude/settings.json` and `~/.codex/hooks.json` SHA-256 hashes were unchanged.
- RTK upgrade:
  - Installed verified latest stable asset `rtk-x86_64-unknown-linux-musl.tar.gz`.
  - Result: `/home/userc/.local/bin/rtk` reports `rtk 0.42.4`.
- Package install:
  - `pipx install --force --editable .`
  - Result: globally installed `rtk-claude-safe 0.4.0`.
- Real global `init` run twice:
  - Both runs reported Claude and Codex hooks already current.
  - `~/.claude/settings.json` SHA-256 stayed
    `0b3718987b58048d8e42f799ab31b976f07c19ba214b3162f39e384483f445a1`.
  - `~/.codex/hooks.json` SHA-256 stayed
    `9a3ec55124b61ec228c5f112ce161b6e636aef8264ab8f0d894159e97910d8b7`.
- Installed hook shape:
  - Claude managed hooks: 75 scoped candidate entries.
  - Codex managed hooks: 1 `^Bash$` hook.
  - Both point at `/home/userc/.local/share/pipx/venvs/rtk-claude-safe/bin/rtk-claude-safe`.

## Direct Hook Smoke Tests

- Codex:
  - `git status` -> `rtk git status`
  - `ls` -> empty stdout
  - `git diff` -> empty stdout
  - `curl https://example.com` -> empty stdout
  - `git status | cat` -> empty stdout
  - non-Bash payload -> empty stdout
- Claude:
  - `git status` -> `rtk git status`
  - `git diff` -> empty stdout
- Real RTK wrapped commands:
  - `rtk git status --short` executed successfully.
  - `rtk git diff --stat` executed successfully.

## RTK Gain Snapshot

- Command used: `rtk gain --history`
- Final snapshot:
  - Total commands: 516
  - Input tokens: 648.0K
  - Output tokens: 36.2K
  - Tokens saved: 612.0K (94.4%)
- Delta during final smoke stage:
  - Total commands increased from 514 to 516.
  - Tokens saved stayed at 612.0K because the final smoke commands were tiny `git status` /
    `git diff --stat` checks with effectively zero savings.
- Note: RTK still prints `[warn] No hook installed`; that is RTK's own hook detector and does not
  recognize `rtk-claude-safe` managed hooks.

## Manual Residual Step

Interactive Codex CLI trust cannot be completed non-interactively. Open Codex CLI, run `/hooks`,
trust the `rtk-claude-safe` hook if prompted, then ask Codex to run `git status`; expected rewritten
command is `rtk git status`.

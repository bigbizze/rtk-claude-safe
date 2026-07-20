# v0.5.0 Verification Results

Recorded: 2026-07-20

Branch: `master`

## Scope

This change keeps `rtk-claude-safe init` unchanged and adds explicit Codex SQLite maintenance
commands:

- `rtk-claude-safe repair-codex-sqlite` installs the managed
  `codex_ignore_low_level_logs` trigger, which ignores `TRACE`, `DEBUG`, and `INFO` log inserts
  before SQLite writes them.
- `rtk-claude-safe revert-codex-sqlite-repair` removes only that exact managed trigger. Missing
  trigger state is idempotent; same-name different SQL is treated as a conflict.
- `rtk-claude-safe vacuum-codex-sqlite` runs `PRAGMA wal_checkpoint(TRUNCATE)`, `VACUUM`, and
  `PRAGMA optimize`. `--backup` writes an adjacent timestamped SQLite backup first.

All three commands ask the user to close Codex CLI sessions, require an exact `Y` confirmation,
detect native Codex and Node wrapper processes, and check again immediately before mutation. The
database path resolves from `--database`, `CODEX_SQLITE_HOME`, `CODEX_HOME`, then
`~/.codex/logs_2.sqlite`; missing databases are not created. Native Windows Codex SQLite
maintenance remains unsupported.

The existing RTK compatibility baseline is unchanged: this repository is still updated against
RTK `v0.42.4` and requires `rtk >= 0.42.4` for hook config mutation.

## Automated Checks

- `uv run --extra dev pytest -q`
  - Result: passed
  - Coverage: 251 tests
- `python3 -m compileall rtk_claude_safe`
  - Result: passed
- `git diff --check`
  - Result: passed
- `python3 -m rtk_claude_safe --version`
  - Result: `rtk-claude-safe 0.5.0`
- `uv run rtk-claude-safe --help`
  - Result: public commands include `repair-codex-sqlite`,
    `revert-codex-sqlite-repair`, and `vacuum-codex-sqlite`; hidden hook commands remain hidden.

## Manual Residual Step

No real repair, revert, or vacuum command was run against `~/.codex/logs_2.sqlite` during
implementation. Those commands intentionally require the operator to close all Codex CLI sessions
and confirm with `Y` before touching the live database.

## Paperclip

- Recorded local operational friction as `obs_19117b79aa85ba92f04b15e8`: Codex can keep inserting
  low-level rows into `~/.codex/logs_2.sqlite`, so this package now exposes guarded repair, revert,
  and vacuum commands instead of changing `init`.

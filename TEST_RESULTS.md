# v0.2.0 Verification Results

Recorded: 2026-06-22T22:27:07-04:00

Branch: `codex-safe-hooks-v0.2.0`

## Automated Checks

- `/tmp/rtk-claude-safe-venv/bin/python -m pytest`
  - Result: passed
  - Coverage: 69 tests
- `/tmp/rtk-claude-safe-venv/bin/python -m compileall rtk_claude_safe`
  - Result: passed
- `git diff --check origin/master...HEAD`
  - Result: passed

## Direct Hook Smoke Tests

- Codex rewrite:
  - Command: `printf '%s\n' '{"tool_name":"Bash","tool_input":{"command":"git status"}}' | rtk-claude-safe codex-hook`
  - Result: emitted `updatedInput.command = "rtk git status"`
- Codex fail-open bypass probes:
  - `ls`: empty output
  - `git diff`: empty output
  - `curl https://example.com`: empty output
  - `git status && git diff --stat`: empty output
  - `env curl https://example.com`: empty output
  - `rtk git status`: empty output

## Idempotency And Upgrade Probes

- Previous-version Claude settings using `rtk hook claude` scoped entries:
  - First current-version patch: changed
  - Second current-version patch: unchanged
  - Final Claude hook command set: `rtk-claude-safe claude-hook`
- Stale Codex hook command path:
  - First current-version patch: changed
  - Second current-version patch: unchanged
  - Final Codex hook command: `/new/rtk-claude-safe codex-hook`

## Review Passes

- Branch-diff review pass 1: found three Codex issues; fixed in `6116ee6`.
- Branch-diff review pass 2: no actionable findings.
- Full-repository review pass 1: found five release blockers; fixed in `be4a479`.
- Full-repository review pass 2: found three hardening issues; fixed in `0c6f504`.
- Full-repository review pass 3: found two idempotency/docs issues; fixed in `91c5103`.
- Full-repository review pass 4: no actionable findings.

## Residual Manual Step

Interactive Codex CLI trust cannot be completed in CI. After install, open Codex CLI, run `/hooks`,
trust the `rtk-claude-safe` hook, then ask Codex to run `git status`; expected rewritten command is
`rtk git status`.

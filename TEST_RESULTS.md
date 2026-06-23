# v0.3.0 Verification Results

Recorded: 2026-06-23T01:16:01-04:00

Branch: `codex/conservative-safe-rewrites`

## Automated Checks

- `/tmp/rtk-claude-safe-venv/bin/python -m pytest`
  - Result: passed
  - Coverage: 179 tests
- `python3 -m compileall rtk_claude_safe`
  - Result: passed
- `git diff --check master...HEAD`
  - Result: passed
- `/tmp/rtk-claude-safe-venv/bin/rtk-claude-safe --version`
  - Result: `rtk-claude-safe 0.3.0`
  - Public help shows only `init`; hidden hook entrypoints are not exposed.

## Direct Hook Smoke Tests

- Codex hook:
  - Allow cases per hook: 13
  - Deny/fail-open cases per hook: 20
  - Rewrites emit `permissionDecision: "allow"` plus `updatedInput.command`.
- Claude hook:
  - Allow cases per hook: 13
  - Deny/fail-open cases per hook: 20
  - Rewrites emit `permissionDecision: "ask"` plus `updatedInput.command`.
- Both hooks preserve unrelated `tool_input` fields.
- Non-Bash Codex payloads emit empty stdout.

Representative rewritten commands:

- `git status` -> `rtk git status`
- `git log --oneline -n 20` -> `rtk git log --oneline -n 20`
- `gh pr view 123` -> `rtk gh pr view 123`
- `pip show pytest` -> `rtk pip show pytest`
- `eslint .` -> `rtk lint .`
- `npx vitest run` -> `rtk npx vitest run`

Representative fail-open commands:

- `git commit -m test`
- `git push`
- `git diff --name-only`
- `npm run dev`
- `cargo build --message-format=json`
- `gh pr view 1 --json=title`
- `gh pr view 1 --comments`
- `gh pr view 1 -wc`
- `gh repo view --web`
- `eslint . -f=json-with-metadata`
- `pnpm exec vitest run --reporter json-summary`
- `prisma migrate reset --force`

## Global Install And Idempotency

- `pipx install --force --editable .`
  - Result: installed global `rtk-claude-safe 0.3.0`
- `rtk-claude-safe init` run twice against real global config:
  - `~/.claude/settings.json` SHA-256 unchanged after both runs.
  - `~/.codex/hooks.json` SHA-256 unchanged after both runs.
  - Codex managed hooks: exactly 1.
  - Claude managed hooks: 75 scoped candidate entries.
  - Managed commands point at `/home/userc/.local/share/pipx/venvs/rtk-claude-safe/bin/rtk-claude-safe`.
- Global hook smoke test:
  - Result: passed
  - Codex rewrites use `permissionDecision: "allow"`.
  - Claude rewrites use `permissionDecision: "ask"`.

## Review Loop

- Initial branch-diff review loop found no remaining actionable findings after targeted fixes.
- Final post-validation review loop findings addressed:
  - Deny unsafe `gh pr/issue view --comments`.
  - Restrict Prisma migrate rewrites.
  - Deny `gh` `-c`, `-q`, `-t`, `--web`, clustered, and attached shorthand variants.
  - Preserve Claude permission flow with `permissionDecision: "ask"`.
  - Deny JSON-like formatter/reporter outputs such as `json-with-metadata` and `json-summary`.
  - Fail open when `rtk` is missing at hook runtime.
- Final review pass:
  - Result: no actionable findings worth addressing.
  - Residual risk: the review subagent could not run pytest in its environment, but it verified compileall and diff whitespace; the main validation environment ran the full suite.

## RTK Gain Snapshot

- Installed RTK: `rtk 0.37.2`
- Command used: `rtk gain -H`
  - `rtk gain history` is not supported by this installed RTK version.
- Snapshot:
  - Total commands: 341
  - Input tokens: 628.7K
  - Output tokens: 19.3K
  - Tokens saved: 609.6K (97.0%)
- Note: `rtk gain -H` prints `[warn] No hook installed`; this is RTK's own hook detection and does
  not recognize the `rtk-claude-safe` managed hooks.

## Manual Residual Step

Interactive Codex CLI trust cannot be completed in CI. After install, open Codex CLI, run `/hooks`,
trust the `rtk-claude-safe` hook, then ask Codex to run `git status`; expected rewritten command is
`rtk git status`.

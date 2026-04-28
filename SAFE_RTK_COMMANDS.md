# rtk PreToolUse Whitelist Audit (rtk-ai/rtk, current master ~v0.37.x / dev-0.38)

This audit was done against `rtk-ai/rtk` master at the end of April 2026 (latest stable v0.37.1, pre-release `dev-0.38.0-rc.176` cut 26 Apr 2026), plus the open issue tracker (368 open bugs at audit time, 161 explicitly tagged `filter-quality`), the `CHANGELOG.md`, the `CLAUDE.md`/`CONTRIBUTING.md` design-philosophy docs, and the `src/cmds/` ecosystem layout. The goal was to expand your 19-entry conservative whitelist to the full set of subcommands that are actually safe under a `rtk hook claude` PreToolUse, given your TS/React/Node + Postgres + Rust + Python stack and your hard requirement that filters never silently drop semantically important information.

## 1. Methodology, in one paragraph

`src/cmds/` is organized as `git/`, `rust/`, `js/`, `python/`, `go/`, `dotnet/`, `cloud/`, `system/`, plus a `ruby/` ecosystem and a built-in TOML filter chain in `core/toml_filter` that resolves `.rtk/filters.toml` (project) → `~/.config/rtk/filters.toml` (user) → built-in. Each Rust filter module pairs with a registry rule in `src/discover/rules.rs`/`registry.rs`, which is the single source of truth for what the hook rewrites. The README headline "100+ commands supported" is real but slightly inflated — the Commands enum in `src/main.rs` has roughly 40 hand-written Rust modules, plus ~25 declarative TOML filters added in v0.27 (the "TOML Part 2/3" PRs). For each command I cross-checked: (a) is there a known open bug labelled `filter-quality` or `bug` against it? (b) does its filter parse-and-re-emit (high-risk) or just collapse/strip (low-risk)? (c) does the maintainer dogfood it in `CLAUDE.md`? (d) does the design philosophy in `CONTRIBUTING.md` (flag-aware, fail-safe to raw, valid-subset-of-real-output) actually hold for that filter today?

A few cross-cutting observations shape every recommendation below:

- **rtk preserves exit codes** for everything (`std::process::exit(child_code)`), which is the strongest single safety guarantee — failed commands stay failed in the agent's view.
- **rtk has a tee-on-failure system** (`[tee] mode = "failures"` is the default, kept for ~5–20 files): when a wrapped command exits non-zero, the *full* unfiltered stdout+stderr is dropped to `~/.local/share/rtk/tee/…log` and the filter output ends with `[full output: …]`. This means even an over-aggressive filter (e.g. Playwright per #690) is recoverable on failure if the user/agent reads the tee log. Take this into account when deciding whether to whitelist a borderline test runner.
- **rtk has a known piped-output corruption class of bug (#1282, #838, #1486)** that affects `rtk grep`, `rtk find`, `rtk ls`, and `git diff --stat` when stdout is not a TTY. As of audit date (2026-04-28) this is *still open* — there is no `isatty` passthrough. Anything you whitelist that an agent might pipe into `wc`, `jq`, `awk`, or another LLM subagent is unsafe by default.
- **rtk has a class of "ls family" bugs** (#1418, #1475, #1448, #1342, #1321, #1276, #803) — `rtk ls` is broken on macOS, on any non-English locale, when `ls` is aliased to `eza`/`exa`/`lsd`, and with the `-1` flag. The maintainer pattern of `Command::new("ls").env("LC_ALL", "C")` is not yet applied. Hard exclude.
- **rtk hook only fires on Bash tool calls** in Claude Code; Read/Grep/Glob bypass entirely. So the whitelist only matters for things the agent actually shells out for.

## 2. Comprehensive command table

Classification key:
- **SAFE** — well-trodden filter, error-preserving, no open correctness bugs (or only cosmetic ones), maintainer dogfoods or low issue volume.
- **CONDITIONAL** — safe for some flag/argument shapes but not all; whitelist with care or with flag-specific gating.
- **UNSAFE** — open silent-correctness bug, or filter strategy is fundamentally lossy in a way the LLM can't detect.

| Command | Class | Key issues / signals | Justification |
|---|---|---|---|
| **Rust / cargo (`src/cmds/rust/`)** | | | |
| `cargo build` | SAFE | dogfooded by maintainer (CLAUDE.md) | Errors-only filter, preserves rustc diagnostics, exit code propagated. Already in your whitelist. |
| `cargo test` | SAFE | flag-aware (`-- --nocapture` preserves all output per CONTRIBUTING.md), tee on fail | Already in your whitelist. Note the `--nocapture` design-philosophy guarantee. |
| `cargo clippy` | SAFE | dogfooded (CLAUDE.md "ALWAYS cargo clippy --all-targets") | Already in your whitelist. |
| `cargo check` | SAFE | passthrough-style filter (errors only) | Same parser family as `cargo build`. |
| `cargo run` | SAFE | passthrough through `run_passthrough()` (`src/cargo_cmd.rs:551`) | Treated as proxy; only token-tracks. |
| `cargo fmt --check` / `cargo fmt --all --check` | SAFE | output is just a file list when there are issues | Maintainer's pre-commit pipeline. |
| `cargo doc` | SAFE | low-risk, errors-only filter | Output is mostly progress + warnings. |
| `cargo nextest` | SAFE | added in v0.18; failures-only | Same parser tier as cargo test. |
| `cargo install` | SAFE | dedicated filter, idempotent collapse ("already installed", "replaced", "from path") | v0.12 feat(cargo) explicit. |
| **JS/TS (`src/cmds/js/`)** | | | |
| `vitest` / `vitest run` | SAFE | failures-only, JSON parser, tee-on-fail | Already in your whitelist. Coverage is an open gap (#220). |
| `vitest run --coverage` | UNSAFE | #220 — coverage data is silently dropped | Pass through with `rtk proxy vitest run --coverage` if needed. |
| `jest` | CONDITIONAL | #1345 — `rtk vitest` handler tries to parse Jest's JSON shape, falls through uncompressed; **harmless** (no corruption) but no savings | Safe to whitelist; you just won't get compression on Jest-only repos. |
| `pnpm test` | CONDITIONAL | #1345 — when pnpm test resolves to Jest in a monorepo, rule routes to `rtk vitest` and falls through. Harmless. | Whitelist it; output is raw or correctly-filtered Vitest. |
| `pnpm install` | SAFE | structured "X packages installed in Ys" summary, exit code preserved | No open bugs. |
| `pnpm run <script>` | SAFE | passthrough w/ `rtk err`-style noise stripping | Safe by construction (the underlying script's runner does the filtering if it's a known one). |
| `pnpm list` / `pnpm outdated` | SAFE | uses OutputParser (`src/cmds/js/pnpm_cmd.rs`) for compact dependency tree | Maintainer's stated example. |
| `pnpm build` / `pnpm lint` | SAFE | rewrites prefix and routes to `rtk err` (per #294 hook expansion) | Failure-preserving wrapper. |
| `npm install` / `npm run` / `npm test` | SAFE | same parser family as pnpm | Treated symmetrically. |
| `npx <known>` (tsc, eslint, prisma, biome, playwright) | SAFE | hook strips `npx` prefix and routes to specific filter | Confirmed in `src/discover/registry.rs` lines ~2565-2655. |
| `npx <unknown>` (e.g. `npx ctx7@latest`) | UNSAFE | #1080 — currently routed to `rtk npx` which incorrectly converts to `npm run`, fails with ENOENT | Add to `[hooks] exclude_commands = ["npx"]` until #1080 closes, *or* whitelist only specific known-good `npx <X>` patterns. |
| `tsc` / `tsc --noEmit` | SAFE | grouped-by-file errors, `TscHandler` is a documented production example | Already in your whitelist. |
| `tsc --watch` | UNSAFE | long-running, streaming output; tee-on-fail semantics don't apply because process never exits | Don't wrap (and rtk doesn't really claim to). |
| `eslint` (no `--fix`) | SAFE | grouped by rule/file, JSON parser | Already in your whitelist. |
| `eslint --fix` | CONDITIONAL | side effects (writes files) matter more than output; filter is still safe but the *value* of the wrapper is low | Whitelist is fine, but consider gating only the read-only invocation. |
| `biome check` / `biome lint` | SAFE | routed via `rtk lint` dispatcher (PR #100 family) | Same JSON parser family as ESLint. |
| `prettier --check` | SAFE | "files needing formatting" — terse list, no semantic loss | Already in your whitelist. |
| `prettier --write` | CONDITIONAL | side-effect command; output is uninteresting; the wrapper just adds tracking | Skip. |
| `next build` | SAFE | dedicated filter strips ANSI/progress, errors only | Already in your whitelist. |
| `next dev` / `next start` | UNSAFE | long-running server; PreToolUse rewrite is meaningless | Don't wrap. |
| `playwright test` | CONDITIONAL | #690 — on **failure**, strips DOM snapshots / locator details / call log; the maintainer's own README config example shows `[hooks] exclude_commands = ["playwright"]` as a *recommended* exclusion | Either whitelist it and rely on `[tee] mode="failures"` to recover the raw log, or — safer — keep it excluded. |
| `playwright install` / `playwright codegen` | SAFE | passthrough | No filter logic worth running. |
| `prisma generate` | SAFE | dedicated filter strips Prisma's ASCII art only | No open bugs. |
| `prisma migrate dev` / `prisma db push` | SAFE | passthrough w/ progress bars stripped | Errors are preserved. |
| `prisma studio` | UNSAFE | long-running server | Don't wrap. |
| **Python (`src/cmds/python/`)** | | | |
| `pytest` | SAFE | state-machine parser shows first 5 failures + summary, tee-on-fail | Already in your whitelist. |
| `mypy` | SAFE | added by `rtk lint mypy` dispatcher (PR #100); also commonly routed via `rtk err` per #294 | Either form is failure-preserving. |
| `ruff check` | SAFE | JSON parser, grouped by rule | Already in your whitelist. |
| `ruff format --check` | SAFE | universal format command (PR #100) | Same family as ruff check. |
| `pip list` / `pip outdated` | SAFE | auto-detects `uv`; compact dependency tree | Low risk. |
| `pip install` | SAFE | strip "Using cached..." noise, errors preserved | Low risk. |
| `uv run pytest` / `uv run ruff` / `uv run mypy` | SAFE | hook expansion in #294 routes these to the underlying filter; `uv run mypy` specifically goes through `rtk err uv run mypy` | Failure-preserving. |
| **Go (`src/cmds/go/`)** — *user does not use Go* | | | |
| `go test` | SAFE | NDJSON streaming parser, 90% savings | Already in your whitelist (you can drop it; you don't use Go). |
| `go build` / `go vet` / `golangci-lint` | SAFE | NDJSON / JSON parsers | Drop from whitelist if you don't use Go. |
| **.NET (`src/cmds/dotnet/`)** — *user does not use .NET* | | | |
| `dotnet test` / `dotnet build` / `dotnet format` | SAFE | trx/binlog parsers | Already in your whitelist. Drop if unused. |
| **Git (`src/cmds/git/`)** | | | |
| `git status` | SAFE | "X modified, Y added, Z untracked" stat extraction; in-progress state is being added in PR #1480 | Already in your whitelist. Note: still subject to truncation per #1313 if the working tree has hundreds of changes. |
| `git log` (incl. `--oneline`, `-n N`) | SAFE | one-line per commit; the v0.18 `git log --oneline regression dropping commits` (#619) is **fixed** | Already in your whitelist. |
| `git add` | SAFE | collapses to `ok` | Already in your whitelist. |
| `git commit` | SAFE | collapses to `ok <sha>`; supports multiple `-m` (#194) | Already in your whitelist. |
| `git push` | SAFE | collapses to `ok <branch>`; exit code propagated (#234) | Already in your whitelist. |
| `git pull` | SAFE | collapses to `ok N files +X -Y`; exit code propagated (#234) | Already in your whitelist. |
| `git fetch` | SAFE | exit code propagated (#234), errors preserved | Add. |
| `git branch` (list/create) | SAFE | branch creation no longer swallowed (#194 fixed in v0.18) | Add. |
| `git checkout` / `git switch` | SAFE | passthrough w/ noise stripping; errors preserved | Add. |
| `git stash` (list/push/pop/apply/show/drop) | SAFE | exit code propagated (#234) | Add. |
| `git rebase` / `git merge` / `git cherry-pick` | SAFE | conflict markers and "CONFLICT (...)" lines preserved (filter is keep-errors) | Add. |
| `git show <commit>` | SAFE | blob detection in `src/cmds/git/mod.rs` ensures binary blobs are summarized, not dumped | Add. |
| `git diff` (no args, `--stat`, `--name-only`) | CONDITIONAL | #1486 (open) — `git diff --stat` silently dropped when piped; #1215 (closed Apr 2026) — `git diff -- <path>` from subdir clap bug; #1313 truncation; v0.34 already removed truncation in `condense_unified_diff` | Whitelist for "what changed" surveys; **avoid** for code review or any pipeline that pipes the diff anywhere. Use `rtk proxy git diff` when reviewing. |
| `git diff <ref>..<ref>` for review | UNSAFE (in spirit) | same truncation + #1486 piped corruption | Use `rtk proxy git diff …` for any code review. |
| `git worktree` | SAFE | exit code propagated (#234) | Add. |
| **GitHub CLI (`src/cmds/git/gh_cmd.rs`)** | | | |
| `gh pr list` | SAFE | compact JSON-driven listing | Add. |
| `gh issue list` | SAFE | compact JSON-driven listing | Add. |
| `gh run list` | SAFE | workflow run status table | Add. |
| `gh repo view` / `gh repo list` | SAFE | "repo" subcommand documented | Add. |
| `gh run view` | SAFE | flags `--log-failed`, `--log`, `--json` preserved (#159 fixed) | Add. |
| `gh workflow list` / `gh workflow view` | SAFE | small structured output | Add. |
| `gh pr view <N>` (no flags) | SAFE | smart markdown body filter (#188 fix) | Add — but see next row. |
| `gh pr view <N> --comments` | UNSAFE | #720 (open, priority:high) — `--json number,title,state,author,body,url` is hardcoded so `--comments` returns *no comments* | Either gate to `gh pr view <N>` without `--comments`, or rely on `gh pr view --comments` falling out of the rewrite (#720 proposes treating `--comments` as a passthrough trigger like `--json`/`--jq`/`--web`). |
| `gh issue view <N>` (no flags) | SAFE | same body filter as PR view | Add. |
| `gh issue view <N> --comments` | UNSAFE | #720 same root cause | Same gate. |
| `gh pr status` | UNSAFE | #960 (open) — fails with `Unknown JSON field: "currentBranch"` | Exclude until #960 closes. |
| `gh ... --json` / `gh ... --jq` / `gh ... --template` / `gh ... --web` | SAFE | hook explicitly skips rewrite for these (#196 / v0.27) | Already correct; documenting for completeness. |
| `gh ... -R <repo>` from outside a git repo | UNSAFE | #223 (open) — fails because the filter shells out to `git` | Use `rtk proxy gh …` or omit from whitelist if you query GitHub from non-repo dirs. |
| `gh project ...` | SAFE-ish | #1093 (open enhancement) — falls through to raw `gh` (no filter, no harm) | Whitelist; just no savings yet. |
| `gh pr edit` / `gh pr comment` | SAFE | #332 fix (correct subcommand routing) | Add. |
| **System / file ops (`src/cmds/system/`)** | | | |
| `ls` | UNSAFE | #1418, #1475, #1448, #1342, #1321, #1276, #803 — empty output on macOS, non-English locales, eza/exa/lsd alias, `-1` flag | **Hard exclude.** Add `"ls"` to `[hooks] exclude_commands` in `~/.config/rtk/config.toml`. |
| `tree` | SAFE | proxy-only, native flag passthrough; same NOISE_DIRS as `ls` but no fragile parser | Add — useful for project structure. |
| `read` (cat/head/tail) | CONDITIONAL | #464 (closed, fixed in v0.34: default is now no filtering, binary file detection added); #1104 (open) — doesn't honor Claude Code's start_line/end_line range | **Default level = SAFE** since v0.34. **`-l aggressive` = CONDITIONAL** (strips bodies; fine for navigation, lossy for reading). |
| `read -l aggressive` | CONDITIONAL | by design strips function bodies | Don't whitelist as a default rewrite for `cat`; only use explicitly. |
| `smart` | SAFE | 2-line heuristic summary, only invoked explicitly | Add as standalone (not as default `cat` rewrite). |
| `find` | CONDITIONAL | #1282 (open) — silent corruption when piped (output format is grouped-by-dir, not one-path-per-line) | Whitelist for **interactive/discovery use only**; if the agent ever pipes find output into xargs/wc/jq, it will produce wrong results. The registry already declines to rewrite find inside pipes (per `hooks` README), so in practice this is *safer than grep*. |
| `grep` (no pipe) | CONDITIONAL | #1282 piped corruption; #838 (open) — `cat foo.log \| rtk grep …` ignores stdin and grep's the codebase instead; #229 BRE alternation bugs | Whitelist only for *file* arguments (e.g. `rtk grep "pattern" .`), and add a hook-level pipe guard the way #1282's reporter recommends. The registry strips `-r` and translates BRE `|` (#206) — fine. |
| `grep` piped (`cmd \| grep ...`) | UNSAFE | #1282 + #838 | Hook should not rewrite the right side of a pipe (the registry already aims for this; verify with `rtk rewrite "cmd \| grep x"`). |
| `wc` | SAFE | v0.34 fix — `wc` was previously not even invoked by the hook; now properly registered, and the filter is purely cosmetic (strip paths/padding) | Add. |
| `diff` (the standalone `rtk diff file1 file2`) | CONDITIONAL | #427 (open, P2) — Claude Code complains "rtk proxy is summarizing the diff", #1313 truncation class | OK for "is the file the same" yes/no checks; not OK for review. Don't include as a wrapper for the system `diff` invoked by tooling. |
| `json` | UNSAFE | this is the same code path that #1015 (unquoted JSON keys) and #1152 (curl JSON destruction) trip on | The schema-replacement is *intentional* but inappropriate as a default rewrite for arbitrary `cat file.json` use. Don't whitelist `rtk json` as a hook rewrite. |
| `env` | SAFE | masks secrets, categorizes variables (`src/cmds/system/env_cmd.rs`) | Add. |
| `log` | SAFE | dedup repeated lines with counter; lossy by design but the design is well-understood | Add — handy for app log triage. |
| `deps` | SAFE | summarizes lockfiles | Add. |
| `summary <cmd>` | SAFE | heuristic line-count + tail | Add. |
| `err <cmd>` | SAFE | shows stderr only, exit code preserved | Add — this is your generic safety wrapper for any command not natively supported. |
| `test <cmd>` | SAFE | generic test wrapper, failures only, tee-on-fail | Add. |
| `format <file>` | SAFE | language-aware formatter wrapper | Niche; OK to add. |
| **Cloud / Infra (`src/cmds/cloud/`)** — *user does not use these heavily* | | | |
| `curl` | UNSAFE | #1152 (JSON value destruction), #1015 (unquoted JSON keys), README example explicitly excludes it (`[hooks] exclude_commands = ["curl"]`) | Hard exclude. |
| `wget` | SAFE | strips progress bars, prints `ok / file / size` | Optional add. |
| `docker ps` / `docker images` / `docker logs` / `docker compose ps`/`logs`/`build` | SAFE | dedicated filter, only listed compose subcommands rewritten (#336 fix) | Optional add (you don't use docker heavily). |
| `kubectl get` / `kubectl logs` | SAFE | dedicated filters | Optional. |
| `aws ...` (25 subcommands as of v0.35: cloudwatch logs, cloudformation events, lambda, iam, dynamodb (with type unwrapping), ecs, ec2 sgs, s3api, s3 sync/cp, eks, sqs, secrets manager) | SAFE | v0.35 expansion + shared `run_aws_filtered()` | Optional. |
| `psql` | CONDITIONAL | #917 (open) — `-h` flag conflict with env-var-prefixed commands; otherwise filter is sound | If you only use `psql` interactively from VSCode/locally, whitelist it; if Claude runs ad-hoc psql with `-h <host> -U <user>`, exclude. |
| **TOML built-ins (added in v0.27 PRs #351, #386, later)** | | | |
| `helm` / `gcloud` / `ansible-playbook` / `pre-commit` / `pio-run` / `mvn` / `gradle` / `tofu` / `terraform plan/init/validate/fmt` / `du` / `df` / `ps` / `systemctl` / `yamllint` / `markdownlint` / `mix` / `shopify-theme` / `iptables` / `fail2ban-client` / `swift` / `shellcheck` / `hadolint` / `poetry` / `composer` / `brew` / `rsync` / `ping` / `stat` / `uv` | SAFE-ish | declarative filters: 8 primitives only (strip_ansi, replace, match_output, strip/keep_lines, truncate_lines_at, head/tail_lines, max_lines, on_empty); cannot do schema replacement, so the cargo-curl-style class of bug is structurally impossible | None of these are in your stack except possibly `brew` and `du`/`df`/`ps`. Optional adds. |
| **Maintenance / control plane** | | | |
| `gain` / `gain --history` / `gain --graph` / `gain --daily` | SAFE | read-only, never wrapped via hook anyway | n/a |
| `discover` / `session` | SAFE | n/a (read-only) | #1055 (open) — `discover` reports false negatives when hook is active, but harmless for whitelisting. |
| `init` / `telemetry` / `proxy` / `verify` | SAFE | control commands | n/a |
| `hook claude` / `hook gemini` / `hook copilot` | n/a | the hook entry-points themselves | n/a |
| `rewrite <cmd>` | SAFE | the registry oracle | useful for debugging your own whitelist (`rtk rewrite "git diff -- foo.ts"` shows you what would actually run). |

## 3. Drop-in additions to `~/.claude/settings.json`

Append the following entries to your existing 19-entry `PreToolUse[*]` array. Each is one line, with a single-line justification next to it. I've grouped them by ecosystem and ordered them by how much value they're likely to add for *your* stack first.

```json
{
  "hooks": {
    "PreToolUse": [
      // --- pnpm / npm / npx (TypeScript stack) ---
      { "type": "command", "command": "rtk hook claude", "if": "Bash(pnpm install*)" },
      { "type": "command", "command": "rtk hook claude", "if": "Bash(pnpm run *)" },
      { "type": "command", "command": "rtk hook claude", "if": "Bash(pnpm test*)" },
      { "type": "command", "command": "rtk hook claude", "if": "Bash(pnpm lint*)" },
      { "type": "command", "command": "rtk hook claude", "if": "Bash(pnpm build*)" },
      { "type": "command", "command": "rtk hook claude", "if": "Bash(pnpm list*)" },
      { "type": "command", "command": "rtk hook claude", "if": "Bash(pnpm outdated*)" },
      { "type": "command", "command": "rtk hook claude", "if": "Bash(pnpm exec *)" },
      { "type": "command", "command": "rtk hook claude", "if": "Bash(pnpm -*)" },
      { "type": "command", "command": "rtk hook claude", "if": "Bash(npm install*)" },
      { "type": "command", "command": "rtk hook claude", "if": "Bash(npm run *)" },
      { "type": "command", "command": "rtk hook claude", "if": "Bash(npx tsc*)" },
      { "type": "command", "command": "rtk hook claude", "if": "Bash(npx eslint*)" },
      { "type": "command", "command": "rtk hook claude", "if": "Bash(npx prisma*)" },
      { "type": "command", "command": "rtk hook claude", "if": "Bash(npx biome*)" },
      { "type": "command", "command": "rtk hook claude", "if": "Bash(npx prettier*)" },
      { "type": "command", "command": "rtk hook claude", "if": "Bash(npx vitest*)" },
      { "type": "command", "command": "rtk hook claude", "if": "Bash(npx playwright codegen*)" },

      // --- Next.js / Prisma / linters (build-only or read-only) ---
      { "type": "command", "command": "rtk hook claude", "if": "Bash(prisma generate*)" },
      { "type": "command", "command": "rtk hook claude", "if": "Bash(prisma migrate*)" },
      { "type": "command", "command": "rtk hook claude", "if": "Bash(prisma db push*)" },
      { "type": "command", "command": "rtk hook claude", "if": "Bash(biome check*)" },
      { "type": "command", "command": "rtk hook claude", "if": "Bash(biome lint*)" },
      { "type": "command", "command": "rtk hook claude", "if": "Bash(tsc --noEmit*)" },

      // --- Python (mypy added; ruff already in user list) ---
      { "type": "command", "command": "rtk hook claude", "if": "Bash(mypy*)" },
      { "type": "command", "command": "rtk hook claude", "if": "Bash(ruff format --check*)" },

      // --- cargo (Rust) — the maintainer's own dogfooding set ---
      { "type": "command", "command": "rtk hook claude", "if": "Bash(cargo check*)" },
      { "type": "command", "command": "rtk hook claude", "if": "Bash(cargo run*)" },
      { "type": "command", "command": "rtk hook claude", "if": "Bash(cargo fmt --all --check*)" },
      { "type": "command", "command": "rtk hook claude", "if": "Bash(cargo fmt --check*)" },
      { "type": "command", "command": "rtk hook claude", "if": "Bash(cargo doc*)" },
      { "type": "command", "command": "rtk hook claude", "if": "Bash(cargo install*)" },
      { "type": "command", "command": "rtk hook claude", "if": "Bash(cargo nextest*)" },

      // --- git family (exit codes propagated since #234) ---
      { "type": "command", "command": "rtk hook claude", "if": "Bash(git fetch*)" },
      { "type": "command", "command": "rtk hook claude", "if": "Bash(git stash*)" },
      { "type": "command", "command": "rtk hook claude", "if": "Bash(git branch*)" },
      { "type": "command", "command": "rtk hook claude", "if": "Bash(git checkout*)" },
      { "type": "command", "command": "rtk hook claude", "if": "Bash(git switch*)" },
      { "type": "command", "command": "rtk hook claude", "if": "Bash(git merge*)" },
      { "type": "command", "command": "rtk hook claude", "if": "Bash(git rebase*)" },
      { "type": "command", "command": "rtk hook claude", "if": "Bash(git cherry-pick*)" },
      { "type": "command", "command": "rtk hook claude", "if": "Bash(git show *)" },
      { "type": "command", "command": "rtk hook claude", "if": "Bash(git worktree*)" },
      { "type": "command", "command": "rtk hook claude", "if": "Bash(git diff --name-only*)" },
      { "type": "command", "command": "rtk hook claude", "if": "Bash(git diff --stat)" },

      // --- gh (only the safe surfaces; comments + pr status excluded) ---
      { "type": "command", "command": "rtk hook claude", "if": "Bash(gh pr list*)" },
      { "type": "command", "command": "rtk hook claude", "if": "Bash(gh issue list*)" },
      { "type": "command", "command": "rtk hook claude", "if": "Bash(gh run list*)" },
      { "type": "command", "command": "rtk hook claude", "if": "Bash(gh run view*)" },
      { "type": "command", "command": "rtk hook claude", "if": "Bash(gh workflow list*)" },
      { "type": "command", "command": "rtk hook claude", "if": "Bash(gh workflow view*)" },
      { "type": "command", "command": "rtk hook claude", "if": "Bash(gh repo view*)" },
      { "type": "command", "command": "rtk hook claude", "if": "Bash(gh repo list*)" },

      // --- system / utilities (low-risk, error-preserving) ---
      { "type": "command", "command": "rtk hook claude", "if": "Bash(tree*)" },
      { "type": "command", "command": "rtk hook claude", "if": "Bash(wc*)" },
      { "type": "command", "command": "rtk hook claude", "if": "Bash(env*)" },
      { "type": "command", "command": "rtk hook claude", "if": "Bash(rtk err *)" },
      { "type": "command", "command": "rtk hook claude", "if": "Bash(rtk test *)" },
      { "type": "command", "command": "rtk hook claude", "if": "Bash(rtk summary *)" }
    ]
  }
}
```

That's 51 additional entries on top of your 19, for a total of **70**. Roughly half of those are pnpm/npm/npx variants for your TypeScript/Next.js stack and the other half are git/gh/cargo subcommands the maintainer either dogfoods or has had clean issue history on for several minor releases.

If you want to be even more aggressive and also get TOML-filter-only ecosystems (which are structurally low-risk), add: `du`, `df`, `ps`, `systemctl`, `brew`, `yamllint`, `markdownlint`, `pre-commit`. None of those are in scope for your stack except `brew` and `pre-commit`, so I left them out of the list above.

## 4. Explicitly excluded — and why

Add these to `~/.config/rtk/config.toml` rather than to your hook whitelist, so even Claude's literal `cat`/`grep`/`ls`/`curl` invocations never get rewritten:

```toml
[hooks]
exclude_commands = [
  "ls",          # #1418, #1475, #1448, #1342, #1321, #1276, #803 — empty output in many environments
  "curl",        # #1152, #1015 — JSON values destroyed / invalid JSON output (README also recommends this)
  "playwright",  # #690 — strips DOM/locator/call-log on test failure (README also recommends this)
  "rtk json",    # same code path as #1015/#1152 — schema replacement is wrong as a default cat-equivalent
  "next dev",    # long-running, not appropriate for PreToolUse rewrite
  "next start",  # long-running
  "tsc --watch", # long-running, no exit
  "prisma studio" # long-running server
]
```

And these PreToolUse rewrites should NOT be in your settings.json, even though they look tempting:

| Excluded | Why |
|---|---|
| `gh issue view --comments`, `gh pr view --comments` | #720 (open, priority:high) — `--json` field list is hardcoded so `--comments` returns *nothing*; agent will confidently report "no comments" when they exist. |
| `gh pr status` | #960 (open) — fails immediately with `Unknown JSON field: "currentBranch"`. |
| `git diff` (without `--stat` or `--name-only`) | #1486 (open, piped truncation), #1313 (truncation class), #1215 (subdir `--` parsing, only just closed). The `condense_unified_diff` work in v0.34 helps but does not fully fix the lossy-by-design problem for code review. Use `rtk proxy git diff` for review. |
| `npx <unknown-package>` | #1080 (open) — converts unknown `npx <pkg>` to `npm run <pkg>` and fails with ENOENT. Whitelist only the four known-good prefixes (`npx tsc`, `npx eslint`, `npx prisma`, `npx biome`, plus `npx prettier`, `npx vitest`, `npx playwright codegen`). |
| `grep` (when piped to anything) | #1282 (open) + #838 (open). The hook script's pipe-detection is fragile; if you redirect grep output anywhere, expect wrong results. Use `rtk proxy grep …` or raw `grep`. |
| `find` (when piped) | #1282. The registry already declines to rewrite `find` inside pipes by default — verify with `rtk rewrite "find . -name '*.ts' \| xargs wc -l"`. |
| `eslint --fix`, `prettier --write` | side-effect commands; the wrapper just adds tracking and the output is rarely the load-bearing artifact. Not unsafe, but low value. |
| `cat package.json` rewriting to `rtk read package.json` | technically fixed in v0.34 (#822/#827 — default is now no filtering, binary detection added), but historically broken via #464. Safe today but be aware that anything that triggers the `aggressive` level still strips function bodies. |

## 5. Flag-dependent safety considerations

The CONTRIBUTING.md philosophy is "default output gets aggressively compressed; verbose/detailed flags should pass through more content." This is not uniformly implemented yet. The flag-dependent surface as of v0.37.x:

- `cargo test` vs `cargo test -- --nocapture` — the second preserves all output (flag-aware filter, documented). Both are safe.
- `gh ... --json`, `gh ... --jq`, `gh ... --template`, `gh ... --web` — registry skips rewrite entirely (#196). Safe.
- `gh issue view --comments`, `gh pr view --comments` — currently *destructive*; the `--comments` flag does not behave as a passthrough trigger like `--json` does (#720 open). Avoid.
- `git log --oneline` — fixed in #619; stable since v0.18.
- `git diff -- <path>` from a subdirectory — fixed via #1215 in late April 2026. Older rtk binaries (pre-0.37.x) may still hit this.
- `ls -1`, `ls -la` (any `ls` flags really) — broken (#803, #1418). Exclude entirely.
- `vitest run --coverage` — drops coverage data (#220). Use `rtk proxy vitest run --coverage`.
- `read -l aggressive` — by design strips function bodies; never set this as your default `cat` rewrite.
- `prettier --check` (safe) vs `prettier --write` (low-value to wrap; side-effect command).
- `eslint` (safe) vs `eslint --fix` (low-value to wrap; the output is uninteresting and the load-bearing artifact is the file edit).
- `tsc --noEmit` (safe — your most likely invocation) vs `tsc --watch` (don't wrap; long-running).
- `next build` (safe) vs `next dev`/`next start` (don't wrap; long-running servers).
- Anything you redirect to a file or pipe to another program (`>`, `\|`, `<()`, `$()`) — the rewrite hook script in the rtk repo currently does *not* universally detect these; #1282 reporter recommends adding `if [[ "$CMD" =~ [\>\|\`] ]] || [[ "$CMD" == *'$('* ]] || [[ "$CMD" == *'<('* ]]; then exit 0; fi` near the top of `~/.claude/hooks/rtk-rewrite.sh`. Strongly recommended for your setup.

## 6. New since November 2025 (commands you may not have a mental model for)

Reviewing the changelog from v0.18 (~Feb 2026) through v0.37.0 (Apr 17 2026) and the dev-0.38 pre-releases, the additions worth knowing about for your stack are:

- **TOML filter engine (v0.27, "TOML Part 2/3", PRs #299/#351/#386)** — declarative filters with 8 primitives. Adds `ping`, `rsync`, `dotnet` extras, `swift`, `shellcheck`, `hadolint`, `poetry`, `composer`, `brew`, `df`, `ps`, `systemctl`, `yamllint`, `markdownlint`, `uv`, plus later additions for `helm`, `gcloud`, `ansible-playbook`, `pre-commit`, `mvn`, `gradle`, `tofu`/`terraform`, `du`, `fail2ban-client`, `iptables`, `mix`, `pio-run`, `shopify-theme`, `stat`. These are structurally safer than Rust filters because the DSL cannot do schema replacement.
- **AWS CLI expansion (v0.35, Apr 6 2026)** — 8 → 25 subcommands. Not relevant for you but flag for completeness.
- **Read default-no-filtering + binary file detection (v0.34, Mar 26 2026)** — closes #822, #464. Means `rtk read package.json` is safe again as of v0.34+.
- **Diff truncation rework (v0.34)** — `condense_unified_diff` is more accurate; #827 closed. But #1486 (piped) and #1313 (truncation class) are still open.
- **Hook permission verdict system (v0.36, #886)** — RTK now respects Claude Code deny/ask permission rules. Less relevant to whitelist correctness, more relevant to security.
- **Binary native hook with streaming (v0.37, Apr 17 2026, #154/#222)** — replaces the old shell hook with a Rust binary; better support for compound commands and env prefixes. Worth upgrading to ≥ 0.37 if you haven't.
- **`rtk session` (v0.32)** — adoption overview across recent Claude sessions.
- **`rtk wc` registry fix (v0.34)** — `wc` was previously not invoked by the hook at all. Now it is.
- **Lint dispatcher (PR #100)** — `rtk lint <linter>` smart-routes to ruff/pylint/mypy/eslint/biome with a unified output shape.
- **Hook rewrite expansion (#294, ongoing)** — covers `uv run pytest`, `uv run python -m pytest`, `pnpm exec eslint`, `python3 -m ruff`, etc. Useful if your team uses uv.

## 7. Practical advice specific to your stack and use case

Three concrete things that, combined, will give you the most reliable outcome:

1. **Pin to ≥ 0.37.1 (or whatever is current stable when you read this).** Several of the issues cited above were closed in the v0.34–v0.37 window (read-as-cat, diff truncation, git argument parsing, hook-engine refactor). Older binaries are noticeably worse.
2. **Add the pipe-detection bypass at the top of `~/.claude/hooks/rtk-rewrite.sh`** as recommended in the body of #1282. This single change neutralizes the entire piped-output corruption class (#1282, #838, #1486) without waiting for upstream.
3. **Set `[hooks] exclude_commands = ["ls", "curl", "playwright", "rtk json", "next dev", "next start", "prisma studio", "tsc --watch"]` in `~/.config/rtk/config.toml`.** This is a belt-and-braces against the cases where the agent reaches for these names directly even if you didn't put them in your `if`-gated whitelist. *Caveat:* #1335 (open) reports that `exclude_commands` may not be wired through *all* code paths in 0.36.0; verify with `rtk rewrite "ls -la"` — it should print `ls -la` unchanged. If it doesn't, the workaround is to not put those patterns in your whitelist (which is what you're already doing).

Finally, on `git diff` specifically — your previous instinct to avoid it for code review was correct, but it's *fine* for the question "what files changed." The split I'd actually use:

- `git diff --stat` and `git diff --name-only` — whitelist (in the JSON block above).
- `git diff` (default) and `git diff <ref>..<ref>` — **do not** whitelist; rely on Claude calling `rtk proxy git diff` or `git diff \| cat` (the cat redirection bypasses the hook on most setups). The maintainer is aware of this tension via #427 ("the rtk proxy is summarizing the diff") and #1486; an `[diff] no_truncate = true` config flag has been requested in #1313 but is not landed yet.

That's the audit. Bottom line: of the ~40 Rust filter modules and ~25 TOML filters in rtk, roughly two-thirds are safe to whitelist for your stack today, four (`ls`, `curl`, `gh ... --comments`, `npx <unknown>`) have actively wrong behavior you should hard-exclude, and three (`git diff`, `playwright test`, `grep`-when-piped) need conditional treatment. Your existing 19-entry whitelist was good but conservative; the additional ~50 entries above are the high-confidence expansion.
# RTK Safe Command Policy

This repository is currently updated against RTK `v0.42.4` and refuses to mutate agent config when
the installed `rtk` is older, prerelease, or unparseable. The allowlist is not RTK's full rewrite
registry. It is the smaller subset this package is willing to install automatically for Claude Code
and Codex.

The source of truth is `rtk_claude_safe/allowlist.py`.

## How Commands Are Classified

Both agent hooks use the same parsed-command classifier:

1. Deny dynamic expansions and split top-level shell lists on `&&`, `||`, and `;`.
2. Deny unsupported shell syntax before any segment allow rule can match.
3. Parse each segment with `shlex` while retaining enough lexical form to distinguish real shell
   assignments from quoted command words.
4. Separate contiguous leading `NAME=value` assignments from each segment's command.
5. Deny long-running, watch, server, and machine-readable output modes on the underlying command.
6. Run command-family-specific safe predicates, identify the stack, and validate every assignment.
7. Return an explicit RTK rewrite command only when at least one segment is allowlisted and every
   other segment is an explicitly preservable neutral command; otherwise return `None` to fail open.

Fail open means the hook emits no output and the agent runs the original command unchanged. This is
intentional. The package optimizes token use; it is not a security boundary.

## Cross-Cutting Denies

The classifier rejects these forms before allowlist matching:

- Unsupported shell syntax: pipes, redirects, background `&`, grouping, newlines, backticks,
  command substitution, and process substitution.
- Already wrapped commands such as `rtk git status` and `rtk proxy git diff`.
- Watch or server modes: `--watch`, `--watchAll`, `--watch-all`, and package scripts named
  `dev`, `start`, `serve`, `server`, `preview`, `storybook`, or `watch`.
- Machine-readable output: `--json`, `--jq`, `--template`, `--format`, `--porcelain`, `-z`,
  `--null`, `--raw`, `--numstat`, `--name-only`, and `--name-status`.
- JSON-like formatter or reporter output from lint and test tools.

Top-level `&&`, `||`, and `;` shell lists are handled segment by segment. For example,
`cd app && npm run test && git status` rewrites to
`cd app && rtk npm run test && rtk git status`; the unmatched `cd app` segment is preserved
unchanged because `cd` is an explicitly allowed neutral segment. Denied segments such as
already-wrapped `rtk ...`, invalid environment prefixes, watch/server commands, machine-readable
output modes, hard-excluded commands, and arbitrary shell commands make the whole list fail open.

## Curated Environment Prefix Registry

The classifier accepts one or more contiguous leading assignments only when the underlying command
is already allowlisted and every name and value is permitted for its detected stack. Names and
values are exact and case-sensitive.

| Scope | Exact accepted assignments |
| --- | --- |
| Any allowlisted command | `LC_ALL=C` or `LC_ALL=C.UTF-8`; `LANG=C` or `LANG=C.UTF-8`; `NO_COLOR=1` or `NO_COLOR=true`; `FORCE_COLOR=0` |
| Rust, Go, Python, or Node.js | `CI=1` or `CI=true`; `TZ=UTC` |
| Rust/Cargo | `CARGO_TERM_COLOR=never`; `RUST_BACKTRACE=0`, `RUST_BACKTRACE=1`, or `RUST_BACKTRACE=full` |
| Go | `CGO_ENABLED=0` or `CGO_ENABLED=1`; `GOMAXPROCS` as an ASCII decimal integer from 1 through 256; `GOTOOLCHAIN=local`; `GOWORK=auto` or `GOWORK=off` |
| Python | `PYTHONUNBUFFERED=1`; `PYTHONDONTWRITEBYTECODE=1`; `PYTHONHASHSEED=random` or an ASCII decimal integer from 0 through 4294967295 |
| Pytest only | `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1` |
| Node.js | `NODE_ENV=development`, `NODE_ENV=production`, or `NODE_ENV=test`; `NODE_NO_WARNINGS=1`; `NODE_DISABLE_COLORS=1`; each of `npm_config_color`, `npm_config_progress`, `npm_config_audit`, `npm_config_fund`, and `npm_config_update_notifier` set to `false` or `0` |

Assignments are parsed separately and safely re-rendered before RTK:

```text
CARGO_TERM_COLOR=never cargo test
→ CARGO_TERM_COLOR=never rtk cargo test

NO_COLOR=true NODE_ENV=test eslint .
→ NO_COLOR=true NODE_ENV=test rtk lint .

LC_ALL=C git status && CI=1 go test ./...
→ LC_ALL=C rtk git status && CI=1 rtk go test ./...
```

Every rewritable segment of an `&&`, `||`, or `;` list may have its own validated prefix. One
invalid segment makes the complete list fail open. Duplicate names, prefixes without a command,
dynamic values, and assignment-prefixed existing RTK commands are denied.

Unknown names are denied even if their values look harmless. This keeps execution-routing and
option-injection variables out of rewrites, including `PATH`, loader variables such as
`LD_PRELOAD` and `DYLD_INSERT_LIBRARIES`, `NODE_OPTIONS`, `PYTHONPATH`, `PYTEST_ADDOPTS`,
`GOFLAGS`, `GODEBUG`, `RUSTFLAGS`, `RUSTC_WRAPPER`, `CARGO_HOME`, and all `RTK_*` names.
Variable names with different casing are also unknown; npm's accepted names are the exact lowercase
`npm_config_*` spellings shown above.

The supported syntax is a direct assignment prefix. The classifier continues to reject
`env NAME=value command`, `export`, `sudo`, redirects, pipes, multiline scripts, command and process
substitutions, and other unsupported shell forms.

Codex has an explicit narrow-exception contract for preserving specific non-RTK segments in
auto-allowed rewrites. Each exception is named in `allowlist.py`, scoped to Codex, and must inspect
the surrounding shell list. The current exceptions are:

- `gofmt-write-before-go-test`: `gofmt -w` with explicit relative `.go` files may be preserved
  before an `&& go test ...` segment that is rewritten through RTK. It does not apply to Claude,
  non-Go files, parent-directory paths, `||`, or formatter invocations without a following rewritten
  Go test segment. Curated Go environment prefixes are validated independently on the formatter and
  test segments.
- `cargo-fmt-before-cargo-validation`: `cargo fmt` or `cargo fmt --all` may be preserved before an
  immediate `&& cargo test ...`, `&& cargo check ...`, or `&& cargo clippy ...` segment that is
  rewritten through RTK. It does not apply to Claude, alternate formatter flags, `||`, or Cargo
  subcommands outside those validation targets. Curated Rust environment prefixes are validated
  independently on both segments.

## Included Families

These families are included only in the narrower forms implemented in `allowlist.py`:

- Rust: `cargo build`, `cargo test`, `cargo check`, `cargo clippy`, and `cargo fmt --check`
  variants. Cargo JSON message formats are denied.
- JavaScript and TypeScript: named package scripts for `test`, `lint`, `build`, `typecheck`,
  `check`, and format-check style scripts. Broad script names are not accepted.
- `pnpm` direct validation shorthands: exact `pnpm test`, `pnpm lint`, `pnpm build`,
  `pnpm typecheck`, `pnpm test:*`, `pnpm check:*`, and `pnpm boundaries` commands with no extra
  args.
- `pnpm --filter`: exact package-name selectors for `typecheck`, `test`, and `build` only.
  Broader pnpm selector syntax, `--filter=<package>`, repeated filters, custom filtered scripts,
  and extra args are denied.
- `pnpm exec`: selected tools only, such as TypeScript, ESLint, Prettier check, Vitest run, and
  Prisma generate.
- `npx`: selected known tools only, preserving the `npx` invocation in the RTK rewrite.
- Python: `pytest`, `mypy`, `ruff check`, `ruff format --check`, and read-only pip inventory
  commands: `pip list`, `pip outdated`, and `pip show`.
- Git: bounded orientation commands only, including narrow `git status`, `git rev-parse HEAD`,
  current/local branch inspection, bounded `git log --oneline`, `git stash list`,
  `git worktree list`, `git diff --stat`, and `git diff --check`.
- GitHub CLI: safe list/view surfaces such as `gh pr list`, `gh issue list`, `gh pr view`, and
  `gh issue view`. Comment-fetching, JSON, jq, template, web, and unsafe shorthand forms are denied.
- Prisma: `prisma generate`, `prisma db push`, and `prisma migrate dev`.
- Small utilities: `tree`, `wc`, and `env`.

## Default Exclusions

These remain denied even though RTK may expose handlers for some of them:

- `ls`, `grep`, `rg`, `find`, `curl`, `cat`, `head`, `tail`, and broad file-inspection commands.
  Their safe subsets require more context than this package currently has, or they have known
  output-fidelity risks when piped, localized, aliased, binary, or machine-readable.
- `git commit` and `git push`. False-success or output-corruption risk is worse than the token
  savings.
- Broad `git log`, `git stash`, `git worktree`, and `git diff` forms. Only the narrow orientation
  forms above are allowed; diff pathspecs and revisions remain denied.
- `git diff --name-only`, `--name-status`, and other machine-style diff output.
- Broad `npm run *`, `pnpm run *`, `yarn run *`, and broad `pnpm exec *`.
- `dotnet test`, pending stronger current validation.
- `playwright test`, because failure output can omit DOM, locator, and call-log details that agents
  need for diagnosis.
- Native Windows Codex command execution and `codex exec`, because hook dispatch is not treated as
  supported by this package.

## Agent Differences

Claude and Codex both call this package's Python hooks. The main difference is where matching starts:

- Claude settings contain scoped `Bash(<pattern>*)` matcher groups for allowlisted commands,
  permitted first-variable names, and chained positions. The Claude hook still parses and checks
  every raw name, value, stack, and command before rewriting, so settings-level scope is only a
  first pass.
- Codex settings contain one `^Bash$` matcher group. Codex matchers apply to the tool name, so the
  full command policy must live inside the hook executable.

Both agents use the same runtime RTK support check. Public `init` fails closed before config
mutation when RTK is unsupported; hidden hooks fail open at runtime.

## Compatibility Baseline

The minimum supported stable RTK version is declared in `rtk_claude_safe/rtk_runtime.py`:

```python
MIN_SUPPORTED_RTK_VERSION = "0.42.4"
RTK_POLICY_UPDATED_FOR = "v0.42.4"
```

When the policy is refreshed for a newer stable RTK release, update those constants, the tests, and
this document in the same change.

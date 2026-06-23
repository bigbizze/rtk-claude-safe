# RTK Safe Command Policy

This repository is currently updated against RTK `v0.42.4` and refuses to mutate agent config when
the installed `rtk` is older, prerelease, or unparseable. The allowlist is not RTK's full rewrite
registry. It is the smaller subset this package is willing to install automatically for Claude Code
and Codex.

The source of truth is `rtk_claude_safe/allowlist.py`.

## How Commands Are Classified

Both agent hooks use the same parsed-command classifier:

1. Parse the shell command with `shlex`.
2. Deny complex shell composition before any allow rule can match.
3. Deny long-running, watch, server, and machine-readable output modes.
4. Run command-family-specific safe predicates.
5. Return an explicit RTK rewrite command for the current agent, or `None` to fail open.

Fail open means the hook emits no output and the agent runs the original command unchanged. This is
intentional. The package optimizes token use; it is not a security boundary.

## Cross-Cutting Denies

The classifier rejects these forms before allowlist matching:

- Shell composition: pipes, redirects, semicolons, `&`, `&&`, `||`, newlines, backticks,
  command substitution, and process substitution.
- Environment-prefixed commands such as `FOO=bar npm test`.
- Already wrapped commands such as `rtk git status` and `rtk proxy git diff`.
- Watch or server modes: `--watch`, `--watchAll`, `--watch-all`, and package scripts named
  `dev`, `start`, `serve`, `server`, `preview`, `storybook`, or `watch`.
- Machine-readable output: `--json`, `--jq`, `--template`, `--format`, `--porcelain`, `-z`,
  `--null`, `--raw`, `--numstat`, `--name-only`, and `--name-status`.
- JSON-like formatter or reporter output from lint and test tools.

## Included Families

These families are included only in the narrower forms implemented in `allowlist.py`:

- Rust: `cargo build`, `cargo test`, `cargo check`, `cargo clippy`, and `cargo fmt --check`
  variants. Cargo JSON message formats are denied.
- JavaScript and TypeScript: named package scripts for `test`, `lint`, `build`, `typecheck`,
  `check`, and format-check style scripts. Broad script names are not accepted.
- `pnpm exec`: selected tools only, such as TypeScript, ESLint, Prettier check, Vitest run, and
  Prisma generate.
- `npx`: selected known tools only, preserving the `npx` invocation in the RTK rewrite.
- Python: `pytest`, `mypy`, `ruff check`, `ruff format --check`, and read-only pip inventory
  commands: `pip list`, `pip outdated`, and `pip show`.
- Git: bounded orientation commands only, including narrow `git status`, bounded
  `git log --oneline`, `git stash list`, `git stash show --stat`, `git worktree list`, and
  `git diff --stat`.
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
  forms above are allowed.
- `git diff --name-only`, `--name-status`, and other machine-style diff output.
- Broad `npm run *`, `pnpm run *`, `yarn run *`, and broad `pnpm exec *`.
- `dotnet test`, pending stronger current validation.
- `playwright test`, because failure output can omit DOM, locator, and call-log details that agents
  need for diagnosis.
- Native Windows Codex command execution and `codex exec`, because hook dispatch is not treated as
  supported by this package.

## Agent Differences

Claude and Codex both call this package's Python hooks. The main difference is where matching starts:

- Claude settings contain many scoped `Bash(<pattern>*)` matcher groups. The Claude hook still
  parses and checks the raw command before rewriting, so settings-level scope is only a first pass.
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

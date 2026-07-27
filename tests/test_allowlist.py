from __future__ import annotations

import pytest

from rtk_claude_safe.allowlist import (
    ENV_PREFIX_VARIABLE_NAMES,
    build_claude_scoped_hooks,
    is_already_rtk_wrapped,
    is_complex_shell_command,
    matches_allowlist,
    rewrite_command_for_agent,
    should_wrap_command,
)


@pytest.mark.parametrize(
    "command",
    [
        "git status",
        "git status --short",
        "git status --short --branch",
        "git status --branch --short",
        "git status -s",
        "git status -sb",
        "git rev-parse HEAD",
        "git branch --show-current",
        "git branch -vv",
        "git branch --list",
        "git diff --check",
        "git diff --cached --stat",
        "git diff --stat --cached",
        "git log --oneline -n 20",
        "git log -n 20 --oneline",
        "git log --oneline --max-count 20",
        "git log --max-count=20 --oneline",
        "npm run test",
        "npm run typecheck",
        "pnpm run format:check",
        "pnpm exec vitest run",
        "pnpm build",
        "pnpm typecheck",
        "pnpm test:unit",
        "pnpm test:integration",
        "pnpm check:raw-sqlite-boundaries",
        "pnpm boundaries",
        "pnpm --filter @kernel-web-app/persistence typecheck",
        "pnpm --filter @kernel-web-app/persistence test",
        "pnpm --filter @kernel-web-app/web build",
        "cargo test --workspace",
        "gh pr list",
        "gh pr view 123",
        "gh issue view 456",
        "pip list",
        "pip outdated",
        "pip show flask",
        "env",
    ],
)
def test_matches_allowlist(command: str) -> None:
    assert matches_allowlist(command)


@pytest.mark.parametrize(
    "command",
    [
        "ls",
        "curl https://example.com",
        "git diff",
        "gh pr status",
        "npx cowsay",
        "playwright test",
        "env curl https://example.com",
        "bare-command-that-does-not-exist",
    ],
)
def test_does_not_match_allowlist(command: str) -> None:
    assert not matches_allowlist(command)


@pytest.mark.parametrize(
    ("command", "expected"),
    [
        ("git status", "rtk git status"),
        ("git status --short --branch", "rtk git status --short --branch"),
        ("git status --branch --short", "rtk git status --branch --short"),
        ("git status -sb", "rtk git status -sb"),
        ("git rev-parse HEAD", "rtk git rev-parse HEAD"),
        ("git branch --show-current", "rtk git branch --show-current"),
        ("git branch -vv", "rtk git branch -vv"),
        ("git branch --list", "rtk git branch --list"),
        ("git diff --check", "rtk git diff --check"),
        ("git diff --cached --stat", "rtk git diff --cached --stat"),
        ("git diff --stat --cached", "rtk git diff --stat --cached"),
        ("git log --oneline -n 20", "rtk git log --oneline -n 20"),
        ("npm run build:ci", "rtk npm run build:ci"),
        ("pnpm typecheck", "rtk pnpm typecheck"),
        ("pnpm test:unit", "rtk pnpm test:unit"),
        ("pnpm test:integration", "rtk pnpm test:integration"),
        ("pnpm check:raw-sqlite-boundaries", "rtk pnpm check:raw-sqlite-boundaries"),
        ("pnpm boundaries", "rtk pnpm boundaries"),
        (
            "pnpm --filter @kernel-web-app/persistence typecheck",
            "rtk pnpm --filter @kernel-web-app/persistence typecheck",
        ),
        (
            "pnpm --filter @kernel-web-app/persistence test",
            "rtk pnpm --filter @kernel-web-app/persistence test",
        ),
        (
            "pnpm --filter @kernel-web-app/web build",
            "rtk pnpm --filter @kernel-web-app/web build",
        ),
        ("pnpm exec prettier --check .", "rtk pnpm exec prettier --check ."),
        ("pip show flask", "rtk pip show flask"),
        ("eslint .", "rtk lint ."),
        ("npx vitest run", "rtk npx vitest run"),
        ("git status && git diff --stat", "rtk git status && rtk git diff --stat"),
        (
            "cargo fmt --check && git diff --check",
            "rtk cargo fmt --check && rtk git diff --check",
        ),
        (
            "git status --short --branch && git rev-parse HEAD",
            "rtk git status --short --branch && rtk git rev-parse HEAD",
        ),
        ("cd app && npm run test", "cd app && rtk npm run test"),
        ('pytest -k "a && b" && git status', "rtk pytest -k 'a && b' && rtk git status"),
        ("false || git status", "false || rtk git status"),
        (
            "git status; npm run typecheck; git diff --stat",
            "rtk git status ; rtk npm run typecheck ; rtk git diff --stat",
        ),
        ("true && git status", "true && rtk git status"),
        ("cd -- app && git status", "cd -- app && rtk git status"),
    ],
)
def test_rewrite_command_for_agent(command: str, expected: str) -> None:
    assert rewrite_command_for_agent(command, "codex") == expected
    assert rewrite_command_for_agent(command, "claude") == expected


@pytest.mark.parametrize(
    ("assignment", "command"),
    [
        ("LC_ALL=C", "git status"),
        ("LC_ALL=C.UTF-8", "cargo test"),
        ("LANG=C", "go test ./..."),
        ("LANG=C.UTF-8", "pytest"),
        ("NO_COLOR=1", "npm run test"),
        ("NO_COLOR=true", "ruff check ."),
        ("FORCE_COLOR=0", "gh pr list"),
        ("CI=1", "cargo test"),
        ("CI=true", "go test ./..."),
        ("CI=1", "ruff check ."),
        ("CI=true", "pytest"),
        ("CI=1", "npm run test"),
        ("TZ=UTC", "cargo check"),
        ("TZ=UTC", "go test ./..."),
        ("TZ=UTC", "mypy"),
        ("TZ=UTC", "pytest"),
        ("TZ=UTC", "pnpm typecheck"),
        ("CARGO_TERM_COLOR=never", "cargo test"),
        ("RUST_BACKTRACE=0", "cargo test"),
        ("RUST_BACKTRACE=1", "cargo test"),
        ("RUST_BACKTRACE=full", "cargo test"),
        ("CGO_ENABLED=0", "go test ./..."),
        ("CGO_ENABLED=1", "go test ./..."),
        ("GOMAXPROCS=1", "go test ./..."),
        ("GOMAXPROCS=008", "go test ./..."),
        ("GOMAXPROCS=256", "go test ./..."),
        ("GOTOOLCHAIN=local", "go test ./..."),
        ("GOWORK=auto", "go test ./..."),
        ("GOWORK=off", "go test ./..."),
        ("PYTHONUNBUFFERED=1", "ruff check ."),
        ("PYTHONDONTWRITEBYTECODE=1", "pip list"),
        ("PYTHONHASHSEED=random", "mypy"),
        ("PYTHONHASHSEED=0", "pytest"),
        ("PYTHONHASHSEED=00000000000", "pytest"),
        ("PYTHONHASHSEED=4294967295", "pytest"),
        ("PYTEST_DISABLE_PLUGIN_AUTOLOAD=1", "pytest"),
        ("NODE_ENV=development", "npm run test"),
        ("NODE_ENV=production", "pnpm run build"),
        ("NODE_ENV=test", "vitest run"),
        ("NODE_NO_WARNINGS=1", "jest"),
        ("NODE_DISABLE_COLORS=1", "tsc"),
        ("npm_config_color=false", "npm run test"),
        ("npm_config_color=0", "npm run test"),
        ("npm_config_progress=false", "npm run test"),
        ("npm_config_progress=0", "npm run test"),
        ("npm_config_audit=false", "pnpm install"),
        ("npm_config_audit=0", "pnpm install"),
        ("npm_config_fund=false", "npx tsc"),
        ("npm_config_fund=0", "npx tsc"),
        ("npm_config_update_notifier=false", "prisma generate"),
        ("npm_config_update_notifier=0", "prisma generate"),
    ],
)
def test_rewrites_every_curated_environment_assignment(
    assignment: str, command: str
) -> None:
    expected = f"{assignment} rtk {command}"

    assert rewrite_command_for_agent(f"{assignment} {command}", "codex") == expected
    assert rewrite_command_for_agent(f"{assignment} {command}", "claude") == expected


@pytest.mark.parametrize(
    ("command", "expected"),
    [
        (
            "LC_ALL=C CI=1 CARGO_TERM_COLOR=never cargo test --workspace",
            "LC_ALL=C CI=1 CARGO_TERM_COLOR=never rtk cargo test --workspace",
        ),
        (
            "LANG='C.UTF-8' PYTHONHASHSEED='random' pytest tests",
            "LANG=C.UTF-8 PYTHONHASHSEED=random rtk pytest tests",
        ),
        (
            'NO_COLOR="true" NODE_ENV="test" eslint .',
            "NO_COLOR=true NODE_ENV=test rtk lint .",
        ),
        (
            "LC_ALL=C git status && NODE_ENV=test npm run test",
            "LC_ALL=C rtk git status && NODE_ENV=test rtk npm run test",
        ),
        (
            "false || CI=true GOMAXPROCS=8 go test ./...",
            "false || CI=true GOMAXPROCS=8 rtk go test ./...",
        ),
        (
            "CARGO_TERM_COLOR=never cargo test; PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 pytest",
            "CARGO_TERM_COLOR=never rtk cargo test ; "
            "PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 rtk pytest",
        ),
    ],
)
def test_rewrites_multiple_quoted_mapped_and_chained_environment_prefixes(
    command: str, expected: str
) -> None:
    assert rewrite_command_for_agent(command, "codex") == expected
    assert rewrite_command_for_agent(command, "claude") == expected


@pytest.mark.parametrize(
    "command",
    [
        "PATH=/tmp cargo test",
        "LD_PRELOAD=/tmp/hook.so cargo test",
        "DYLD_INSERT_LIBRARIES=/tmp/hook.dylib cargo test",
        "NODE_OPTIONS=--require=hook.js npm run test",
        "PYTHONPATH=src pytest",
        "PYTEST_ADDOPTS=--capture=no pytest",
        "GOFLAGS=-mod=vendor go test ./...",
        "GODEBUG=netdns=go go test ./...",
        "RUSTFLAGS=-Cdebuginfo=0 cargo test",
        "RUSTC_WRAPPER=sccache cargo test",
        "CARGO_HOME=/tmp/cargo cargo test",
        "RTK_CONFIG_PATH=/tmp/rtk.toml cargo test",
        "RTK_SOMETHING=1 npm run test",
        "UNKNOWN=value git status",
    ],
)
def test_rejects_dangerous_or_unknown_environment_names(command: str) -> None:
    assert rewrite_command_for_agent(command, "codex") is None
    assert rewrite_command_for_agent(command, "claude") is None


@pytest.mark.parametrize(
    "command",
    [
        "LC_ALL=en_US.UTF-8 git status",
        "LANG=C.UTF8 git status",
        "NO_COLOR=TRUE git status",
        "FORCE_COLOR=1 git status",
        "CI=0 cargo test",
        "TZ=America/Toronto cargo test",
        "CARGO_TERM_COLOR=always cargo test",
        "RUST_BACKTRACE=2 cargo test",
        "CGO_ENABLED=true go test ./...",
        "GOMAXPROCS=0 go test ./...",
        "GOMAXPROCS=257 go test ./...",
        "GOMAXPROCS=lots go test ./...",
        "GOTOOLCHAIN=auto go test ./...",
        "GOWORK=workspace go test ./...",
        "PYTHONUNBUFFERED=true pytest",
        "PYTHONDONTWRITEBYTECODE=0 pytest",
        "PYTHONHASHSEED=-1 pytest",
        "PYTHONHASHSEED=4294967296 pytest",
        "PYTHONHASHSEED=seed pytest",
        "PYTEST_DISABLE_PLUGIN_AUTOLOAD=true pytest",
        "NODE_ENV=staging npm run test",
        "NODE_NO_WARNINGS=true npm run test",
        "NODE_DISABLE_COLORS=0 npm run test",
        "npm_config_color=true npm run test",
        "npm_config_progress=1 npm run test",
        "npm_config_audit=FALSE npm run test",
        "npm_config_fund=no npm run test",
        "npm_config_update_notifier=yes npm run test",
        "NPM_CONFIG_COLOR=false npm run test",
    ],
)
def test_rejects_invalid_environment_values_and_case_variants(command: str) -> None:
    assert rewrite_command_for_agent(command, "codex") is None
    assert rewrite_command_for_agent(command, "claude") is None


@pytest.mark.parametrize(
    "command",
    [
        "CI=1 git status",
        "TZ=UTC gh pr list",
        "CARGO_TERM_COLOR=never go test ./...",
        "RUST_BACKTRACE=1 pytest",
        "CGO_ENABLED=0 cargo test",
        "GOMAXPROCS=8 npm run test",
        "GOTOOLCHAIN=local pytest",
        "GOWORK=off cargo test",
        "PYTHONUNBUFFERED=1 npm run test",
        "PYTHONDONTWRITEBYTECODE=1 cargo test",
        "PYTHONHASHSEED=random go test ./...",
        "PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 ruff check .",
        "NODE_ENV=test pytest",
        "NODE_NO_WARNINGS=1 cargo test",
        "NODE_DISABLE_COLORS=1 go test ./...",
        "npm_config_color=false cargo test",
    ],
)
def test_rejects_environment_variables_on_the_wrong_stack(command: str) -> None:
    assert rewrite_command_for_agent(command, "codex") is None
    assert rewrite_command_for_agent(command, "claude") is None


@pytest.mark.parametrize(
    "command",
    [
        "LC_ALL=C LC_ALL=C cargo test",
        "CI=1 CI=true cargo test",
        "LC_ALL=C LANG=C LC_ALL=C.UTF-8 git status",
        "LC_ALL=C",
        "LC_ALL=C CI=1",
        "LC_ALL= cargo test",
        "'LC_ALL=C' cargo test",
        '"CI=1" cargo test',
        r"LC_ALL\=C cargo test",
        "L'C'_ALL=C cargo test",
        "CI=$CI cargo test",
        "LANG=${LANG:-C} cargo test",
        "NODE_ENV=$(printf test) npm run test",
        "NO_COLOR=`printf 1` npm run test",
    ],
)
def test_rejects_duplicate_incomplete_or_dynamic_environment_prefixes(command: str) -> None:
    assert rewrite_command_for_agent(command, "codex") is None
    assert rewrite_command_for_agent(command, "claude") is None


@pytest.mark.parametrize(
    "command",
    [
        "CI=1 cargo run",
        "NODE_ENV=test npm run dev",
        "PYTHONUNBUFFERED=1 pytest --json-report",
        "CGO_ENABLED=0 go test -json ./...",
        "LC_ALL=C gh pr view 123 --json title",
        "NO_COLOR=1 vitest --watch",
    ],
)
def test_environment_prefixes_do_not_bypass_existing_command_denies(command: str) -> None:
    assert rewrite_command_for_agent(command, "codex") is None
    assert rewrite_command_for_agent(command, "claude") is None


@pytest.mark.parametrize(
    "command",
    [
        "env LC_ALL=C cargo test",
        "export LC_ALL=C; cargo test",
        "sudo LC_ALL=C cargo test",
        "LC_ALL=C cargo test | cat",
        "LC_ALL=C cargo test > screenshot.txt",
        "LC_ALL=C cargo test && NODE_ENV=staging npm run test",
        "LC_ALL=C git status && CARGO_TERM_COLOR=never rtk cargo test",
        "LC_ALL=C cargo test && curl https://example.com",
        "LC_ALL=C cargo test\nNODE_ENV=test npm run test",
        "LC_ALL=C cargo test\rNODE_ENV=test npm run test",
        "LC_ALL=C cargo test $(printf -- --workspace)",
    ],
)
def test_environment_prefixes_fail_open_for_unsupported_shell_forms_or_segments(
    command: str,
) -> None:
    assert rewrite_command_for_agent(command, "codex") is None
    assert rewrite_command_for_agent(command, "claude") is None


def test_claude_candidate_hooks_cover_safe_git_log_flag_order() -> None:
    hooks = build_claude_scoped_hooks("rtk-claude-safe claude-hook")
    expected_hook = {
        "type": "command",
        "command": "rtk-claude-safe claude-hook",
        "if": "Bash(git log*)",
    }

    assert expected_hook in hooks
    assert rewrite_command_for_agent("git log -n 20 --oneline", "claude") == (
        "rtk git log -n 20 --oneline"
    )


def test_claude_candidate_hooks_cover_new_git_and_pnpm_triggers() -> None:
    hooks = build_claude_scoped_hooks("rtk-claude-safe claude-hook")
    expected_patterns = {
        "Bash(git diff --check*)",
        "Bash(git rev-parse HEAD*)",
        "Bash(git branch --show-current*)",
        "Bash(git branch -vv*)",
        "Bash(git branch --list*)",
        "Bash(pnpm typecheck*)",
        "Bash(pnpm check*)",
        "Bash(pnpm boundaries*)",
        "Bash(pnpm --filter *)",
    }

    hook_patterns = {hook["if"] for hook in hooks}
    assert expected_patterns <= hook_patterns


def test_claude_candidate_hooks_cover_every_environment_first_variable_position() -> None:
    hooks = build_claude_scoped_hooks("rtk-claude-safe claude-hook")
    hook_patterns = {hook["if"] for hook in hooks}

    for name in ENV_PREFIX_VARIABLE_NAMES:
        assert f"Bash({name}=*)" in hook_patterns
        for separator in ("&&", "||", ";"):
            assert f"Bash(*{separator}*{name}=*)" in hook_patterns


def test_claude_environment_candidates_do_not_override_runtime_validation() -> None:
    assert rewrite_command_for_agent("CI=1 git status", "claude") is None
    assert rewrite_command_for_agent("NODE_ENV=test cargo test", "claude") is None
    assert rewrite_command_for_agent("LC_ALL=en_US.UTF-8 git status", "claude") is None


def test_codex_rewrites_gofmt_write_before_go_test_policy_exception() -> None:
    command = "gofmt -w main.go git.go internal/foo.go && go test ./..."

    assert rewrite_command_for_agent(command, "codex") == (
        "gofmt -w main.go git.go internal/foo.go && rtk go test ./..."
    )
    assert rewrite_command_for_agent(command, "claude") is None


@pytest.mark.parametrize(
    "command",
    [
        "gofmt -w main.go",
        "gofmt -w main.go && git status",
        "gofmt -w main.go || go test ./...",
        "gofmt -w main.go && cd app && go test ./...",
        "go test ./... && gofmt -w main.go",
        "gofmt -w main.go && go test -json ./...",
        "gofmt -w ./main.go ../other.go && go test ./...",
        "gofmt -w main.go README.md && go test ./...",
        "gofmt -r 'a -> b' -w main.go && go test ./...",
        "./gofmt -w main.go && go test ./...",
    ],
)
def test_gofmt_policy_exception_stays_narrow(command: str) -> None:
    assert rewrite_command_for_agent(command, "codex") is None


def test_codex_gofmt_policy_exception_accepts_valid_go_environment_prefixes() -> None:
    command = "CGO_ENABLED=0 gofmt -w main.go && CI=1 GOMAXPROCS=8 go test ./..."

    assert rewrite_command_for_agent(command, "codex") == (
        "CGO_ENABLED=0 gofmt -w main.go && CI=1 GOMAXPROCS=8 rtk go test ./..."
    )
    assert rewrite_command_for_agent(command, "claude") is None


@pytest.mark.parametrize(
    ("command", "expected"),
    [
        (
            "cargo fmt && cargo test -q -p kernel section_store",
            "cargo fmt && rtk cargo test -q -p kernel section_store",
        ),
        (
            "cargo fmt --all && cargo test --workspace",
            "cargo fmt --all && rtk cargo test --workspace",
        ),
        (
            "cargo fmt && cargo check -q -p kernel",
            "cargo fmt && rtk cargo check -q -p kernel",
        ),
        (
            "cargo fmt --all && cargo clippy -q --workspace --all-targets -- -D warnings",
            "cargo fmt --all && rtk cargo clippy -q --workspace --all-targets -- -D warnings",
        ),
    ],
)
def test_codex_rewrites_cargo_fmt_before_cargo_validation_policy_exception(
    command: str,
    expected: str,
) -> None:
    assert rewrite_command_for_agent(command, "codex") == expected
    assert rewrite_command_for_agent(command, "claude") is None


@pytest.mark.parametrize(
    "command",
    [
        "cargo fmt",
        "cargo fmt || cargo test",
        "cargo test --workspace && cargo fmt",
        "cargo fmt --package kernel && cargo test -p kernel",
        "cargo fmt --all -- --check && cargo test --workspace",
        "cargo fmt && cargo test --message-format=json",
        "cargo fmt && cargo check --message-format=json",
        "cargo fmt && cargo clippy --message-format=json",
        "cargo fmt && cargo build --workspace",
        "cargo fmt && cargo doc --workspace",
        "cargo fmt && cargo run",
        "./cargo fmt && cargo test",
    ],
)
def test_cargo_fmt_policy_exception_stays_narrow(command: str) -> None:
    assert rewrite_command_for_agent(command, "codex") is None


def test_codex_cargo_fmt_policy_exception_accepts_valid_rust_environment_prefixes() -> None:
    command = (
        "CARGO_TERM_COLOR=never cargo fmt --all && "
        "CI=true RUST_BACKTRACE=full cargo clippy --workspace"
    )

    assert rewrite_command_for_agent(command, "codex") == (
        "CARGO_TERM_COLOR=never cargo fmt --all && "
        "CI=true RUST_BACKTRACE=full rtk cargo clippy --workspace"
    )
    assert rewrite_command_for_agent(command, "claude") is None


@pytest.mark.parametrize(
    "command",
    [
        "PYTHONUNBUFFERED=1 gofmt -w main.go && go test ./...",
        "CGO_ENABLED=2 gofmt -w main.go && go test ./...",
        "NODE_ENV=test cargo fmt && cargo test",
        "RUST_BACKTRACE=verbose cargo fmt && cargo test",
    ],
)
def test_environment_aware_policy_exceptions_stay_stack_and_value_scoped(
    command: str,
) -> None:
    assert rewrite_command_for_agent(command, "codex") is None


@pytest.mark.parametrize(
    "command",
    [
        "git status && git diff --stat",
        "cd app && npm run test",
        "git status; npm run typecheck",
        "false || git status",
        "true && git status",
    ],
)
def test_safe_shell_list_commands_are_rewritten(command: str) -> None:
    assert is_complex_shell_command(command)
    assert should_wrap_command(command)


@pytest.mark.parametrize(
    "command",
    [
        "git status | cat",
        "FOO=bar npm test",
        "echo $(git status)",
        "git status > out.txt",
        "git status & curl https://example.com",
        "git status && (npm test)",
        "git status && npm test > out.txt",
        "git status &&",
        "rtk git status && npm run test",
        "git status && curl https://example.com",
        "FOO=bar npm test && git status",
        "npm run dev && git status",
        "gh pr view 123 --json title && git status",
        "rm -rf ./tmp && git status",
        "git status && { npm run test; }",
        "if true; then npm run test; fi",
        "for f in a; do npm run test; done",
        "cd $APP_DIR && git status",
        "git status && npm run test $NPM_ARGS",
        "git status && npm run test ${NPM_ARGS}",
        "cd ~ && git status",
        "git status && cd ~/repo",
        "npm run test # skip && git status",
        "./cd app && git status",
        "npm run test 'a\\' && git status 'x\\'",
    ],
)
def test_unsafe_shell_commands_are_not_wrapped(command: str) -> None:
    assert is_complex_shell_command(command)
    assert not should_wrap_command(command)


@pytest.mark.parametrize(
    "command",
    [
        'git status "&&" npm run test',
        'git status ";" npm run test',
        'true ";" npm run test',
        'false "||" npm run test',
        r"git status \; npm run test",
        r"git status \&\& npm run test",
    ],
)
def test_quoted_or_escaped_separators_are_not_shell_lists(command: str) -> None:
    assert not is_complex_shell_command(command)
    assert not should_wrap_command(command)


@pytest.mark.parametrize(
    "command",
    [
        "rtk git status",
        "rtk proxy git diff",
        "rtk --whatever git status",
        "/usr/local/bin/rtk git status",
        "LC_ALL=C rtk git status",
        "CI=1 /usr/local/bin/rtk cargo test",
    ],
)
def test_already_rtk_wrapped(command: str) -> None:
    assert is_already_rtk_wrapped(command)
    assert not should_wrap_command(command)


def test_fully_quoted_assignment_word_is_not_treated_as_an_rtk_prefix() -> None:
    assert not is_already_rtk_wrapped("'LC_ALL=C' rtk git status")
    assert not should_wrap_command("'LC_ALL=C' rtk git status")


@pytest.mark.parametrize(
    "command",
    [
        "env curl https://example.com",
        "env FOO=bar npm test",
        'git status "unterminated',
    ],
)
def test_uncertain_or_nested_commands_are_not_wrapped(command: str) -> None:
    assert not should_wrap_command(command)


@pytest.mark.parametrize(
    "command",
    [
        "vitest --watch",
        "tsc --watch",
        "npm run dev",
        "npm run start",
        "pnpm run storybook",
        "pnpm run test:watch",
        "gh pr view 123 --json title",
        "gh pr view 123 --json=title",
        "gh pr view 123 --jq .title",
        "gh pr view 123 --jq=.title",
        "gh pr view 123 --template '{{.title}}'",
        "gh pr view 123 --comments",
        "gh pr view 123 --comments=true",
        "gh pr view 123 -c",
        "gh pr view 123 -c=true",
        "gh pr view 123 -wc",
        "gh pr view 123 -cw",
        "gh issue view 123 --comments",
        "gh issue view 123 -c",
        "gh issue view 123 -c=true",
        "gh pr view 123 --web",
        "gh issue view 123 --web",
        "gh repo view --web",
        "gh run view 123 --web",
        "gh run view 123 -w=true",
        "gh run view 123 -wv",
        "gh issue view 123 -t={{.title}}",
        "gh issue view 123 -t '{{.title}}'",
        "gh pr list -q=.title",
        "gh pr list -q '.[].title'",
        "gh issue view 123 --template='{{.title}}'",
        "pnpm list --json=true",
        "pnpm dev",
        "pnpm start",
        "pnpm test:watch",
        "pnpm verify",
        "pnpm typecheck --watch",
        "pnpm boundaries --fix",
        "pnpm lint:fix",
        "pnpm test:unit -- --filter other",
        "pnpm Test:unit",
        "pnpm CHECK:foo",
        "pnpm TYPECHECK",
        "pnpm BOUNDARIES",
        "PNPM typecheck",
        "/tmp/PNPM typecheck",
        "/tmp/pnpm typecheck",
        "./pnpm test:unit",
        "/usr/bin/pnpm --filter pkg test",
        "Pnpm --filter foo test",
        "pnpm --filter=foo test",
        "pnpm --filter @scope/* test",
        "pnpm --filter ./packages/foo test",
        "pnpm --filter ../foo test",
        "pnpm --filter @-scope/pkg test",
        "pnpm --filter @scope/.pkg test",
        "pnpm --filter @scope/pkg/extra test",
        "pnpm --filter pkg lint",
        "pnpm --filter @scope/pkg test --watch",
        "pnpm --filter @scope/pkg test --reporter=json",
        "pnpm --filter @scope/pkg test --filter other",
        "git status --porcelain",
        "git status --branch",
        "git diff --name-only",
        "git diff --stat --name-only",
        "git diff --stat --patch-with-stat",
        "git diff --stat --binary",
        "git diff --stat --full-index",
        "git diff --stat README.md",
        "git diff --stat -- README.md",
        "git diff --stat HEAD",
        "git diff --cached --stat README.md",
        "git diff --check -- README.md",
        "git diff --raw",
        "git rev-parse --abbrev-ref HEAD",
        "git rev-parse HEAD --short",
        "git branch -r",
        "git branch --list foo",
        "git branch --show-current --quiet",
        "git log --oneline",
        "git log --oneline -n 100",
        "git log --oneline --format=%H",
        "git log --oneline --stat -n 10",
        "git commit -m test",
        "git push",
        "git stash push",
        "git worktree add ../other",
        "dotnet test",
        "cargo build --message-format=json",
        "cargo test --message-format json",
        "vitest run --coverage",
        "vitest run --reporter=json",
        "vitest run --reporter=junit",
        "vitest run --reporter json",
        "vitest run --reporter json-summary",
        "vitest run --outputFile report.json",
        "eslint . -f json",
        "eslint . -f=json",
        "eslint . -f json-with-metadata",
        "npx eslint . -f json-with-metadata",
        "pnpm exec eslint . -f json-with-metadata",
        "npx vitest run --reporter json-summary",
        "pnpm exec vitest run --reporter json-summary",
        "npm run test -- --reporter json-summary",
        "pnpm run test -- --reporter json-summary",
        "ruff check . --output-format json",
        "ruff check . --output-format=github",
        "go test -json ./...",
        "go test -json=true ./...",
        "pytest --json-report",
        "pytest --junitxml=report.xml",
        "pytest --cov-report=xml",
        "mypy --junit-xml report.xml",
        "mypy --junit-xml=report.xml",
        "pnpm exec vitest --watch",
        "pnpm exec prisma migrate dev",
        "prisma migrate reset --force",
        "prisma migrate deploy",
        "npx prisma migrate reset --force",
        "npx prisma migrate deploy",
        "biome check",
    ],
)
def test_risky_subsets_are_not_wrapped(command: str) -> None:
    assert rewrite_command_for_agent(command, "codex") is None
    assert not should_wrap_command(command)

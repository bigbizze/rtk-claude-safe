"""Shared safe command rewrite policy for Claude and Codex adapters."""

from __future__ import annotations

import re
import shlex
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Literal

Agent = Literal["claude", "codex"]

# Candidate patterns decide when Claude should invoke the wrapper. The runtime
# classifier below is the final authority for whether a command is rewritten.
CANDIDATE_PATTERNS: list[str] = [
    # cargo
    "cargo test*",
    "cargo build*",
    "cargo clippy*",
    "cargo check*",
    "cargo fmt --all --check*",
    "cargo fmt --check*",
    "cargo doc*",
    "cargo nextest*",
    # generic test/lint/typecheck runners
    "vitest*",
    "jest*",
    "pytest*",
    "go test*",
    "tsc*",
    "eslint*",
    "ruff check*",
    "ruff format --check*",
    "mypy*",
    "prettier --check*",
    "next build*",
    # pnpm / npm
    "pnpm install*",
    "pnpm run test*",
    "pnpm run lint*",
    "pnpm run build*",
    "pnpm run typecheck*",
    "pnpm run check*",
    "pnpm run format:check*",
    "pnpm test*",
    "pnpm lint*",
    "pnpm build*",
    "pnpm list*",
    "pnpm outdated*",
    "pnpm exec tsc*",
    "pnpm exec eslint*",
    "pnpm exec prettier --check*",
    "pnpm exec vitest run*",
    "pnpm exec prisma generate*",
    "npm install*",
    "npm run test*",
    "npm run lint*",
    "npm run build*",
    "npm run typecheck*",
    "npm run check*",
    "npm run format:check*",
    # npx
    "npx tsc*",
    "npx eslint*",
    "npx prisma generate*",
    "npx prisma migrate*",
    "npx prisma db push*",
    "npx prettier --check*",
    "npx vitest run*",
    "npx playwright codegen*",
    # prisma
    "prisma generate*",
    "prisma migrate*",
    "prisma db push*",
    # git
    "git status*",
    "git log*",
    "git stash list*",
    "git worktree list*",
    "git diff --stat*",
    # gh
    "gh pr list*",
    "gh pr view*",
    "gh issue list*",
    "gh issue view*",
    "gh run list*",
    "gh run view*",
    "gh workflow list*",
    "gh workflow view*",
    "gh repo view*",
    "gh repo list*",
    # misc
    "tree*",
    "wc*",
    "env",
    "pip list*",
    "pip outdated*",
    "pip show*",
]

def _build_claude_candidate_patterns() -> list[str]:
    patterns = list(CANDIDATE_PATTERNS)
    for separator in ("&&", "||", ";"):
        patterns.extend(f"*{separator}*{pattern}" for pattern in CANDIDATE_PATTERNS)
    return patterns


# Compatibility name for callers that imported the scoped pattern list.
SCOPED_PATTERNS = _build_claude_candidate_patterns()

_SHELL_PUNCTUATION_CHARS = "|&;()<>"
_UNSAFE_SHELL_EXPANSIONS = ("\n", "`", "$", "<(", ">(")
_PRESERVABLE_SHELL_LIST_COMMANDS = {"cd", "true", "false", ":"}
_UNSAFE_PRESERVED_ARG_CHARS = "$`*?[]{};&|<>!"
_ENV_PREFIX_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")
_MACHINE_OUTPUT_FLAGS = {
    "--json",
    "--jq",
    "--template",
    "--format",
    "--name-only",
    "--raw",
    "--numstat",
    "--name-status",
    "--output",
    "--outputFile",
    "--output-file",
    "--junit-xml",
    "--junitxml",
    "-json",
    "--parseable",
    "-z",
    "--null",
    "-q",
    "-t",
}
_INLINE_MACHINE_OUTPUT_FLAGS = {
    "--json",
    "--jq",
    "--template",
    "--format",
    "--porcelain",
    "--output",
    "--outputFile",
    "--output-file",
    "--output-format",
}
_MACHINE_FORMAT_VALUE_FLAGS = {"-f", "--reporter", "--output-format"}
_MACHINE_FORMAT_VALUES = {
    "json",
    "json-lines",
    "github",
    "gitlab",
    "junit",
    "junit-xml",
    "xml",
    "sarif",
    "checkstyle",
    "tap",
}
_MACHINE_REPORT_PREFIXES = (
    "--junit-xml",
    "--junitxml",
    "--cov-report",
    "--json-report",
    "--json-report-file",
    "--xml-report",
    "--html-report",
    "--linecount-report",
    "--any-exprs-report",
    "--cobertura-xml-report",
    "--txt-report",
)
_WATCH_FLAGS = {"-w", "--watch", "--watch-all", "--watchAll"}
_SERVER_SCRIPT_WORDS = {"dev", "start", "serve", "server", "preview", "storybook", "watch"}
_SAFE_SCRIPT_ROOTS = {"test", "lint", "build", "typecheck", "check", "format:check"}


@dataclass(frozen=True)
class ShellListPolicyException:
    """Narrow opt-in exception for preserving a non-RTK segment in a shell list."""

    name: str
    agents: frozenset[Agent]
    can_preserve: Callable[[list[str], list[str], list[str], list[str]], bool]


def _can_preserve_gofmt_before_go_test(
    parts: list[str],
    _following_rewrites: list[str],
    remaining_segments: list[str],
    remaining_separators: list[str],
) -> bool:
    if not _safe_gofmt_write_args(parts):
        return False
    if not remaining_segments or not remaining_separators or remaining_separators[0] != "&&":
        return False
    next_parts = _split_command(remaining_segments[0])
    if not next_parts:
        return False
    next_rewrite = _rewrite_segment(next_parts, "codex")
    return next_rewrite is not None and next_rewrite.startswith("rtk go test")


CODEX_SHELL_LIST_POLICY_EXCEPTIONS: tuple[ShellListPolicyException, ...] = (
    ShellListPolicyException(
        name="gofmt-write-before-go-test",
        agents=frozenset({"codex"}),
        can_preserve=_can_preserve_gofmt_before_go_test,
    ),
)


def is_complex_shell_command(command: str) -> bool:
    """Return True when a command contains shell syntax beyond one simple command."""
    stripped = command.strip()
    if not stripped:
        return False
    if _ENV_PREFIX_RE.match(stripped):
        return True
    if _has_unsafe_shell_expansion(stripped):
        return True
    shell_list = _split_shell_list(stripped)
    if shell_list is None:
        return _has_unquoted_shell_syntax(stripped)
    _segments, separators = shell_list
    return bool(separators)


def is_already_rtk_wrapped(command: str) -> bool:
    """Return True when the command already starts with the rtk executable."""
    parts = _split_command(command)
    if not parts:
        return False
    executable = Path(parts[0]).name.lower()
    return executable in {"rtk", "rtk.exe"}


def _split_command(command: str) -> list[str] | None:
    try:
        return shlex.split(command.strip())
    except ValueError:
        return None


def _join(parts: list[str]) -> str:
    return shlex.join(parts)


def _rtk_prefix(parts: list[str]) -> str:
    return _join(["rtk", *parts])


def matches_allowlist(command: str) -> bool:
    """Return True when the command has a safe RTK rewrite."""
    return rewrite_command_for_agent(command, "codex") is not None


def should_wrap_command(command: str) -> bool:
    """Return True when a Bash command should be rewritten through rtk."""
    return rewrite_command_for_agent(command, "codex") is not None


def rewrite_command_for_agent(command: str, agent: Agent = "codex") -> str | None:
    """Return the concrete RTK rewrite for an agent, or None to fail open."""
    if _has_unsafe_shell_expansion(command):
        return None

    shell_list = _split_shell_list(command)
    if shell_list is None:
        return None

    segments, separators = shell_list
    if not separators:
        parts = _split_command(segments[0])
        if not parts:
            return None
        return _rewrite_segment(parts, agent)

    return _rewrite_shell_list(segments, separators, agent)


def _split_shell_list(command: str) -> tuple[list[str], list[str]] | None:
    stripped = command.strip()
    if not stripped:
        return None

    segments: list[str] = []
    separators: list[str] = []
    quote: str | None = None
    escaped = False
    segment_start = 0
    index = 0

    while index < len(stripped):
        char = stripped[index]
        if escaped:
            escaped = False
            index += 1
            continue
        if char == "\\":
            escaped = True
            index += 1
            continue
        if quote is not None:
            if char == quote:
                quote = None
            index += 1
            continue
        if char in {"'", '"'}:
            quote = char
            index += 1
            continue
        if char == ";":
            if not _append_shell_segment(stripped, segment_start, index, segments):
                return None
            separators.append(";")
            segment_start = index + 1
            index += 1
            continue
        if char == "&":
            if index + 1 < len(stripped) and stripped[index + 1] == "&":
                if index + 2 < len(stripped) and stripped[index + 2] == "&":
                    return None
                if not _append_shell_segment(stripped, segment_start, index, segments):
                    return None
                separators.append("&&")
                segment_start = index + 2
                index += 2
                continue
            return None
        if char == "|":
            if index + 1 < len(stripped) and stripped[index + 1] == "|":
                if index + 2 < len(stripped) and stripped[index + 2] == "|":
                    return None
                if not _append_shell_segment(stripped, segment_start, index, segments):
                    return None
                separators.append("||")
                segment_start = index + 2
                index += 2
                continue
            return None
        if char in "<>()":
            return None
        index += 1

    if quote is not None or escaped:
        return None
    if not _append_shell_segment(stripped, segment_start, len(stripped), segments):
        return None
    return segments, separators


def _append_shell_segment(command: str, start: int, end: int, segments: list[str]) -> bool:
    segment = command[start:end].strip()
    if not segment:
        return False
    segments.append(segment)
    return True


def _rewrite_shell_list(segments: list[str], separators: list[str], agent: Agent) -> str | None:
    segment_parts: list[list[str]] = []
    for segment in segments:
        parts = _split_command(segment)
        if not parts:
            return None
        segment_parts.append(parts)

    rewritten_segments: list[str | None] = []
    changed = False
    for segment in segment_parts:
        rewrite = _rewrite_segment(segment, agent)
        if rewrite is None:
            if not _can_preserve_shell_list_segment(segment):
                rewritten_segments.append(None)
                continue
            rewrite = _join(segment)
        else:
            changed = True
        rewritten_segments.append(rewrite)

    if not changed:
        return None

    for index, rewrite in enumerate(rewritten_segments):
        if rewrite is not None:
            continue
        segment = segment_parts[index]
        if not _can_apply_shell_list_policy_exception(
            agent,
            segment,
            [item for item in rewritten_segments[index + 1 :] if item is not None],
            segments[index + 1 :],
            separators[index:],
        ):
            return None
        rewritten_segments[index] = _join(segment)

    if any(segment is None for segment in rewritten_segments):
        return None

    materialized_segments = [segment for segment in rewritten_segments if segment is not None]
    result = materialized_segments[0]
    for separator, segment in zip(separators, materialized_segments[1:]):
        result = f"{result} {separator} {segment}"
    return result


def _can_apply_shell_list_policy_exception(
    agent: Agent,
    parts: list[str],
    rewritten_segments: list[str],
    remaining_segments: list[str],
    remaining_separators: list[str],
) -> bool:
    return any(
        agent in exception.agents
        and exception.can_preserve(
            parts,
            rewritten_segments,
            remaining_segments,
            remaining_separators,
        )
        for exception in CODEX_SHELL_LIST_POLICY_EXCEPTIONS
    )


def _rewrite_segment(parts: list[str], agent: Agent) -> str | None:
    if not parts:
        return None
    if _has_env_assignment_prefix(parts):
        return None
    if Path(parts[0]).name.lower() == "env" and len(parts) > 1:
        return None
    if _is_rtk_wrapped_parts(parts):
        return None
    if _has_common_deny(parts):
        return None
    return _rewrite_parts(parts, agent)


def _has_unsafe_shell_expansion(command: str) -> bool:
    return any(token in command for token in _UNSAFE_SHELL_EXPANSIONS)


def _has_unquoted_shell_syntax(command: str) -> bool:
    quote: str | None = None
    escaped = False
    for char in command:
        if escaped:
            escaped = False
            continue
        if char == "\\":
            escaped = True
            continue
        if quote is not None:
            if char == quote:
                quote = None
            continue
        if char in {"'", '"'}:
            quote = char
            continue
        if char in _SHELL_PUNCTUATION_CHARS:
            return True
    return False


def _has_env_assignment_prefix(parts: list[str]) -> bool:
    return bool(parts and _ENV_PREFIX_RE.match(parts[0]))


def _can_preserve_shell_list_segment(parts: list[str]) -> bool:
    if not parts or _has_env_assignment_prefix(parts):
        return False
    command = Path(parts[0]).name.lower()
    if command not in _PRESERVABLE_SHELL_LIST_COMMANDS:
        return False
    if command == "cd":
        return _safe_cd_args(parts[1:])
    return len(parts) == 1


def _safe_cd_args(args: list[str]) -> bool:
    if not args or args == ["-"]:
        return True
    if args[0] == "--":
        args = args[1:]
        if not args:
            return True
    if len(args) != 1 or args[0].startswith(("-", "~")):
        return False
    return not any(char in args[0] for char in _UNSAFE_PRESERVED_ARG_CHARS)


def _safe_gofmt_write_args(parts: list[str]) -> bool:
    if len(parts) < 3 or Path(parts[0]).name.lower() != "gofmt":
        return False
    if parts[1] != "-w":
        return False
    return all(_safe_gofmt_path(arg) for arg in parts[2:])


def _safe_gofmt_path(arg: str) -> bool:
    if not arg.endswith(".go"):
        return False
    if arg.startswith(("-", "~", "/")):
        return False
    if ".." in Path(arg).parts:
        return False
    return not any(char in arg for char in _UNSAFE_PRESERVED_ARG_CHARS)


def _is_rtk_wrapped_parts(parts: list[str]) -> bool:
    executable = Path(parts[0]).name.lower()
    return executable in {"rtk", "rtk.exe"}


def _has_common_deny(parts: list[str]) -> bool:
    return _has_machine_output_flag(parts) or _has_watch_flag(parts)


def _has_machine_output_flag(parts: list[str]) -> bool:
    for index, part in enumerate(parts[1:], start=1):
        if part in _MACHINE_OUTPUT_FLAGS:
            return True
        if "=" in part:
            flag, value = part.split("=", 1)
            if flag in _INLINE_MACHINE_OUTPUT_FLAGS:
                return True
            if flag == "-json":
                return True
            if flag in _MACHINE_FORMAT_VALUE_FLAGS and _is_machine_format_value(value):
                return True
            if flag == "--reporter" and value.startswith("json"):
                return True
            if flag.startswith(_MACHINE_REPORT_PREFIXES):
                return True
        if part.startswith("--porcelain") or part.startswith("--json-report"):
            return True
        if part.startswith(_MACHINE_REPORT_PREFIXES):
            return True
        if part.startswith("--coverage"):
            return True
        if (
            part in _MACHINE_FORMAT_VALUE_FLAGS
            and index + 1 < len(parts)
            and _is_machine_format_value(parts[index + 1])
        ):
            return True
    return False


def _is_machine_format_value(value: str) -> bool:
    lowered = value.lower()
    return lowered in _MACHINE_FORMAT_VALUES or lowered.startswith("json")


def _has_watch_flag(parts: list[str]) -> bool:
    for part in parts[1:]:
        if part in _WATCH_FLAGS or part.startswith("--watch"):
            return True
    return False


def _rewrite_parts(parts: list[str], agent: Agent) -> str | None:
    command = Path(parts[0]).name.lower()
    if command == "cargo":
        return _rewrite_cargo(parts)
    if command == "git":
        return _rewrite_git(parts)
    if command in {"npm", "pnpm"}:
        return _rewrite_package_manager(parts)
    if command == "npx":
        return _rewrite_npx(parts)
    if command == "gh":
        return _rewrite_gh(parts)
    if command == "prisma":
        return _rewrite_prisma(parts)
    if command == "pip":
        return _rewrite_pip(parts)
    if command == "eslint":
        return _rewrite_eslint(parts)
    if command == "biome":
        return None
    if command in {"vitest", "jest", "pytest", "tsc", "mypy"}:
        if _has_test_runner_deny(parts):
            return None
        return _rtk_prefix(parts)
    if command == "go" and len(parts) > 1 and parts[1] == "test":
        return None if any(p.startswith("-bench") for p in parts[2:]) else _rtk_prefix(parts)
    if command == "ruff":
        return _rewrite_ruff(parts)
    if command == "prettier":
        return _rtk_prefix(parts) if len(parts) > 1 and parts[1] == "--check" else None
    if command == "next":
        return _rtk_prefix(parts) if len(parts) > 1 and parts[1] == "build" else None
    if command in {"tree", "wc"}:
        return _rtk_prefix(parts)
    if command == "env" and len(parts) == 1:
        return _rtk_prefix(parts)
    return None


def _rewrite_cargo(parts: list[str]) -> str | None:
    if len(parts) < 2:
        return None
    if _has_cargo_json_message_format(parts[2:]):
        return None
    subcommand = parts[1]
    if subcommand in {"test", "build", "clippy", "check", "doc", "nextest"}:
        return _rtk_prefix(parts)
    if parts[1:] == ["fmt", "--check"] or parts[1:] == ["fmt", "--all", "--check"]:
        return _rtk_prefix(parts)
    return None


def _has_cargo_json_message_format(args: list[str]) -> bool:
    for index, arg in enumerate(args):
        if arg == "--message-format":
            return index + 1 < len(args) and args[index + 1].startswith("json")
        if arg.startswith("--message-format=json"):
            return True
    return False


def _rewrite_git(parts: list[str]) -> str | None:
    if len(parts) < 2:
        return None
    subcommand = parts[1]
    args = parts[2:]
    if subcommand == "status":
        return _rtk_prefix(parts) if args in ([], ["--short"], ["-s"]) else None
    if subcommand == "log":
        return _rtk_prefix(parts) if _safe_git_log_args(args) else None
    if subcommand == "stash":
        return _rtk_prefix(parts) if args == ["list"] else None
    if subcommand == "worktree":
        return _rtk_prefix(parts) if args == ["list"] else None
    if subcommand == "diff":
        return _rtk_prefix(parts) if _safe_git_diff_args(args) else None
    return None


def _safe_git_diff_args(args: list[str]) -> bool:
    if not args or args[0] != "--stat":
        return False
    denied = {
        "--name-only",
        "--name-status",
        "--numstat",
        "--raw",
        "--patch",
        "--patch-with-stat",
        "--patch-with-raw",
        "--binary",
        "--full-index",
        "-p",
    }
    denied_prefixes = ("--patch", "--raw", "--name-", "--numstat", "--binary", "--full-index")
    return not any(arg in denied or arg.startswith(denied_prefixes) for arg in args)


def _safe_git_log_args(args: list[str]) -> bool:
    if "--oneline" not in args:
        return False
    denied = {
        "--stat",
        "-p",
        "--patch",
        "--graph",
        "--reverse",
        "--merges",
        "--first-parent",
    }
    if any(arg in denied or arg.startswith("--pretty") for arg in args):
        return False

    count_seen = False
    index = 0
    while index < len(args):
        arg = args[index]
        if arg == "--oneline":
            index += 1
            continue
        if arg in {"-n", "--max-count"}:
            if index + 1 >= len(args) or not _is_small_positive_int(args[index + 1]):
                return False
            count_seen = True
            index += 2
            continue
        if arg.startswith("--max-count="):
            if not _is_small_positive_int(arg.split("=", 1)[1]):
                return False
            count_seen = True
            index += 1
            continue
        return False
    return count_seen


def _is_small_positive_int(value: str, limit: int = 50) -> bool:
    try:
        parsed = int(value, 10)
    except ValueError:
        return False
    return 1 <= parsed <= limit


def _has_test_runner_deny(parts: list[str]) -> bool:
    for index, part in enumerate(parts[1:], start=1):
        if part.startswith("--coverage") or part.startswith("--json-report"):
            return True
        if part in {"--outputFile", "--output-file"}:
            return True
        if part.startswith("--outputFile=") or part.startswith("--output-file="):
            return True
        if part == "--reporter" and index + 1 < len(parts) and parts[index + 1].startswith("json"):
            return True
        if part.startswith("--reporter=json"):
            return True
    return False


def _rewrite_package_manager(parts: list[str]) -> str | None:
    command = Path(parts[0]).name.lower()
    if command == "npm":
        if len(parts) > 1 and parts[1] == "install":
            return _rtk_prefix(parts)
        if len(parts) > 2 and parts[1] == "run" and _safe_package_script(parts[2]):
            return _rtk_prefix(parts)
        return None

    if len(parts) > 1 and parts[1] in {"install", "list", "outdated"}:
        return _rtk_prefix(parts)
    if len(parts) > 1 and parts[1] in {"test", "lint", "build"}:
        return _rtk_prefix(parts)
    if len(parts) > 2 and parts[1] == "run" and _safe_package_script(parts[2]):
        return _rtk_prefix(parts)
    if len(parts) > 2 and parts[1] == "exec":
        return _rewrite_pnpm_exec(parts)
    return None


def _safe_package_script(script: str) -> bool:
    lowered = script.lower()
    if any(word in lowered for word in _SERVER_SCRIPT_WORDS):
        return False
    return any(lowered == root or lowered.startswith(f"{root}:") for root in _SAFE_SCRIPT_ROOTS)


def _rewrite_pnpm_exec(parts: list[str]) -> str | None:
    tool = Path(parts[2]).name.lower()
    rest = parts[3:]
    if tool in {"tsc", "eslint"}:
        return _rtk_prefix(parts)
    if tool == "prettier" and rest and rest[0] == "--check":
        return _rtk_prefix(parts)
    if tool == "vitest" and rest and rest[0] == "run":
        return _rtk_prefix(parts)
    if tool == "prisma" and rest == ["generate"]:
        return _rtk_prefix(parts)
    return None


def _rewrite_npx(parts: list[str]) -> str | None:
    if len(parts) < 2:
        return None
    tool = Path(parts[1]).name.lower()
    rest = parts[2:]
    if tool in {"tsc", "eslint"}:
        return _rtk_prefix(parts)
    if tool == "vitest" and rest and rest[0] == "run":
        return _rtk_prefix(parts)
    if tool == "prettier" and rest and rest[0] == "--check":
        return _rtk_prefix(parts)
    if tool == "playwright" and rest and rest[0] == "codegen":
        return _rtk_prefix(parts)
    if tool == "prisma" and _safe_prisma_args(rest):
        return _rtk_prefix(parts)
    return None


def _rewrite_gh(parts: list[str]) -> str | None:
    if len(parts) < 3:
        return None
    area, action = parts[1], parts[2]
    gh_args = parts[3:]
    if _has_gh_machine_output_shorthand(gh_args):
        return None
    if _has_gh_web_flag(gh_args):
        return None
    if area in {"pr", "issue"} and action == "view" and _has_gh_comments_flag(gh_args):
        return None
    if area == "pr" and action in {"list", "view"}:
        return _rtk_prefix(parts)
    if area == "issue" and action in {"list", "view"}:
        return _rtk_prefix(parts)
    if area == "run" and action in {"list", "view"}:
        return _rtk_prefix(parts)
    if area == "workflow" and action in {"list", "view"}:
        return _rtk_prefix(parts)
    if area == "repo" and action in {"view", "list"}:
        return _rtk_prefix(parts)
    return None


def _has_gh_comments_flag(args: list[str]) -> bool:
    return any(
        _has_short_flag(arg, "c") or arg == "--comments" or arg.startswith("--comments=")
        for arg in args
    )


def _has_gh_web_flag(args: list[str]) -> bool:
    return any(_has_short_flag(arg, "w") or arg == "--web" or arg.startswith("--web=") for arg in args)


def _has_gh_machine_output_shorthand(args: list[str]) -> bool:
    return any(_has_short_flag(arg, "q") or _has_short_flag(arg, "t") for arg in args)


def _has_short_flag(arg: str, flag: str) -> bool:
    if not arg.startswith("-") or arg.startswith("--") or len(arg) < 2:
        return False
    return flag in arg[1:].split("=", 1)[0]


def _rewrite_prisma(parts: list[str]) -> str | None:
    return _rtk_prefix(parts) if _safe_prisma_args(parts[1:]) else None


def _safe_prisma_args(args: list[str]) -> bool:
    if not args:
        return False
    if args[0] == "generate":
        return True
    if len(args) >= 2 and args[:2] in (["db", "push"], ["migrate", "dev"]):
        return True
    return False


def _rewrite_pip(parts: list[str]) -> str | None:
    if len(parts) > 1 and parts[1] in {"list", "outdated", "show"}:
        return _rtk_prefix(parts)
    return None


def _rewrite_eslint(parts: list[str]) -> str | None:
    return _join(["rtk", "lint", *parts[1:]])


def _rewrite_ruff(parts: list[str]) -> str | None:
    if len(parts) > 1 and parts[1] == "check":
        return _rtk_prefix(parts)
    if len(parts) > 2 and parts[1:3] == ["format", "--check"]:
        return _rtk_prefix(parts)
    return None


def build_claude_scoped_hooks(command: str = "rtk hook claude") -> list[dict]:
    """Build Claude Code scoped hook entries from the shared allowlist."""
    return [
        {"type": "command", "command": command, "if": f"Bash({pattern})"}
        for pattern in SCOPED_PATTERNS
    ]

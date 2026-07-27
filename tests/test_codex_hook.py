from __future__ import annotations

import io
import json

import pytest

from rtk_claude_safe import codex_hook


@pytest.fixture(autouse=True)
def _rtk_available(monkeypatch) -> None:
    monkeypatch.setattr(codex_hook, "runtime_rtk_supported", lambda: True)


def _run_hook(payload: str | dict) -> tuple[int, str]:
    stdin = io.StringIO(payload if isinstance(payload, str) else json.dumps(payload))
    stdout = io.StringIO()
    rc = codex_hook.main(stdin=stdin, stdout=stdout)
    return rc, stdout.getvalue()


def test_codex_hook_rewrites_allowlisted_bash_command() -> None:
    rc, output = _run_hook({"tool_name": "Bash", "tool_input": {"command": "git status"}})

    assert rc == 0
    assert json.loads(output) == {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "allow",
            "updatedInput": {"command": "rtk git status"},
        }
    }


def test_codex_hook_uses_mapped_rewrite_command() -> None:
    rc, output = _run_hook(
        {"tool_name": "Bash", "tool_input": {"command": "eslint .", "description": "lint"}}
    )

    assert rc == 0
    assert json.loads(output)["hookSpecificOutput"]["updatedInput"] == {
        "command": "rtk lint .",
        "description": "lint",
    }


def test_codex_hook_rewrites_environment_prefix_and_preserves_extra_input() -> None:
    rc, output = _run_hook(
        {
            "tool_name": "Bash",
            "tool_input": {
                "command": "NO_COLOR=true NODE_ENV=test eslint .",
                "description": "lint without color",
            },
        }
    )

    assert rc == 0
    assert json.loads(output)["hookSpecificOutput"] == {
        "hookEventName": "PreToolUse",
        "permissionDecision": "allow",
        "updatedInput": {
            "command": "NO_COLOR=true NODE_ENV=test rtk lint .",
            "description": "lint without color",
        },
    }


def test_codex_hook_rewrites_allowlisted_segments_in_shell_list() -> None:
    rc, output = _run_hook(
        {"tool_name": "Bash", "tool_input": {"command": "cd app && npm run test && git status"}}
    )

    assert rc == 0
    assert json.loads(output)["hookSpecificOutput"]["updatedInput"] == {
        "command": "cd app && rtk npm run test && rtk git status",
    }


def test_codex_hook_allows_gofmt_policy_exception_before_go_test() -> None:
    rc, output = _run_hook(
        {
            "tool_name": "Bash",
            "tool_input": {"command": "gofmt -w main.go git.go && go test ./..."},
        }
    )

    assert rc == 0
    assert json.loads(output) == {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "allow",
            "updatedInput": {"command": "gofmt -w main.go git.go && rtk go test ./..."},
        }
    }


def test_codex_hook_allows_cargo_fmt_policy_exception_before_cargo_validation() -> None:
    rc, output = _run_hook(
        {
            "tool_name": "Bash",
            "tool_input": {"command": "cargo fmt --all && cargo clippy -q --workspace"},
        }
    )

    assert rc == 0
    assert json.loads(output) == {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "allow",
            "updatedInput": {"command": "cargo fmt --all && rtk cargo clippy -q --workspace"},
        }
    }


def test_codex_hook_rewrites_cargo_fmt_check_before_git_diff_check() -> None:
    rc, output = _run_hook(
        {
            "tool_name": "Bash",
            "tool_input": {"command": "cargo fmt --check && git diff --check"},
        }
    )

    assert rc == 0
    assert json.loads(output) == {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "allow",
            "updatedInput": {
                "command": "rtk cargo fmt --check && rtk git diff --check",
            },
        }
    }


def test_codex_hook_rewrites_filtered_pnpm_validation() -> None:
    rc, output = _run_hook(
        {
            "tool_name": "Bash",
            "tool_input": {"command": "pnpm --filter @kernel-web-app/persistence test"},
        }
    )

    assert rc == 0
    assert json.loads(output) == {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "allow",
            "updatedInput": {
                "command": "rtk pnpm --filter @kernel-web-app/persistence test",
            },
        }
    }


def test_codex_hook_emits_nothing_for_non_allowlisted_command() -> None:
    rc, output = _run_hook({"tool_name": "Bash", "tool_input": {"command": "ls"}})

    assert rc == 0
    assert output == ""


def test_codex_hook_does_not_probe_rtk_for_fail_open_inputs(monkeypatch) -> None:
    def fail() -> bool:
        raise AssertionError("runtime probe should not be called")

    monkeypatch.setattr(codex_hook, "runtime_rtk_supported", fail)

    for payload in [
        "{not json",
        {"tool_name": "apply_patch", "tool_input": {"command": "git status"}},
        {"tool_name": "Bash", "tool_input": {"command": "ls"}},
        {"tool_name": "Bash", "tool_input": {"command": "git status | cat"}},
        {"tool_name": "Bash", "tool_input": {"command": "rtk git status"}},
    ]:
        rc, output = _run_hook(payload)
        assert rc == 0
        assert output == ""


def test_codex_hook_emits_nothing_for_non_bash_tool() -> None:
    rc, output = _run_hook({"tool_name": "apply_patch", "tool_input": {"command": "git status"}})

    assert rc == 0
    assert output == ""


def test_codex_hook_emits_nothing_for_invalid_json() -> None:
    rc, output = _run_hook("{not json")

    assert rc == 0
    assert output == ""


def test_codex_hook_emits_nothing_for_unsafe_shell_command() -> None:
    rc, output = _run_hook(
        {"tool_name": "Bash", "tool_input": {"command": "git status | cat"}}
    )

    assert rc == 0
    assert output == ""


def test_codex_hook_emits_nothing_for_risky_subset() -> None:
    rc, output = _run_hook({"tool_name": "Bash", "tool_input": {"command": "npm run dev"}})

    assert rc == 0
    assert output == ""


def test_codex_hook_emits_nothing_for_already_wrapped_command() -> None:
    rc, output = _run_hook({"tool_name": "Bash", "tool_input": {"command": "rtk git status"}})

    assert rc == 0
    assert output == ""


@pytest.mark.parametrize(
    "command",
    [
        "CI=1 git status",
        "NODE_ENV=staging npm run test",
        "LC_ALL=C rtk git status",
        "LC_ALL=C git status && NODE_ENV=staging npm run test",
    ],
)
def test_codex_hook_emits_nothing_for_invalid_environment_prefixes(command: str) -> None:
    rc, output = _run_hook(
        {"tool_name": "Bash", "tool_input": {"command": command}}
    )

    assert rc == 0
    assert output == ""


def test_codex_hook_emits_nothing_when_rtk_is_missing(monkeypatch) -> None:
    monkeypatch.setattr(codex_hook, "runtime_rtk_supported", lambda: False)

    rc, output = _run_hook({"tool_name": "Bash", "tool_input": {"command": "git status"}})

    assert rc == 0
    assert output == ""

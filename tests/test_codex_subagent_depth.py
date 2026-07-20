from __future__ import annotations

import sqlite3
import threading
import time
from pathlib import Path

import pytest

from rtk_claude_safe.codex_subagent_depth import (
    database_path,
    handle_payload,
    initialize_subagent_depth_state,
    resolve_effective_max_depth,
)


def _session_payload(project: Path, session_id: str = "session") -> dict:
    return {
        "hook_event_name": "SessionStart",
        "session_id": session_id,
        "cwd": str(project),
        "source": "startup",
    }


def _pre_payload(
    project: Path,
    *,
    session_id: str = "session",
    tool_use_id: str = "tool-1",
    agent_id: str | None = None,
) -> dict:
    payload = {
        "hook_event_name": "PreToolUse",
        "session_id": session_id,
        "turn_id": "turn",
        "cwd": str(project),
        "tool_name": "spawn_agent",
        "tool_input": {"agent_type": "reviewer"},
        "tool_use_id": tool_use_id,
    }
    if agent_id is not None:
        payload["agent_id"] = agent_id
    return payload


def _post_payload(
    project: Path,
    *,
    session_id: str = "session",
    tool_use_id: str = "tool-1",
    child_agent_id: str = "agent-1",
    agent_id: str | None = None,
    response_as_string: bool = False,
) -> dict:
    response: object = {"agent_id": child_agent_id, "agent_type": "reviewer"}
    if response_as_string:
        response = '{"agent_id":"%s","agent_type":"reviewer"}' % child_agent_id
    payload = {
        "hook_event_name": "PostToolUse",
        "session_id": session_id,
        "turn_id": "turn",
        "cwd": str(project),
        "tool_name": "spawn_agent",
        "tool_input": {"agent_type": "reviewer"},
        "tool_response": response,
        "tool_use_id": tool_use_id,
    }
    if agent_id is not None:
        payload["agent_id"] = agent_id
    return payload


def _codex_home(tmp_path: Path) -> Path:
    home = tmp_path / ".codex"
    home.mkdir()
    return home


def _write_user_depth(codex_home: Path, max_depth: int | None) -> None:
    if max_depth is None:
        codex_home.joinpath("config.toml").write_text("", encoding="utf-8")
    else:
        codex_home.joinpath("config.toml").write_text(
            f"[agents]\nmax_depth = {max_depth}\n",
            encoding="utf-8",
        )


def _system_config(tmp_path: Path) -> Path:
    return tmp_path / "etc" / "codex" / "config.toml"


def _run(payload: dict, codex_home: Path, system_config: Path, *, wait: float = 0.0):
    return handle_payload(
        payload,
        codex_home=codex_home,
        system_config_path=system_config,
        unknown_agent_wait_seconds=wait,
    )


def _agent_depth(codex_home: Path, agent_id: str) -> int:
    with sqlite3.connect(database_path(codex_home)) as conn:
        row = conn.execute("SELECT depth FROM agents WHERE agent_id = ?", (agent_id,)).fetchone()
    assert row is not None
    return int(row[0])


@pytest.mark.parametrize("max_depth", [0, 1, 2, 3, 4])
def test_enforces_max_depth_independently_for_depths_0_through_4(tmp_path, max_depth: int) -> None:
    codex_home = _codex_home(tmp_path)
    project = tmp_path / "project"
    project.mkdir()
    system_config = _system_config(tmp_path)
    _write_user_depth(codex_home, max_depth)
    initialize_subagent_depth_state(codex_home)

    assert _run(_session_payload(project), codex_home, system_config) is None

    parent_agent_id = None
    for depth in range(max_depth):
        tool_use_id = f"spawn-{depth}"
        assert (
            _run(
                _pre_payload(project, tool_use_id=tool_use_id, agent_id=parent_agent_id),
                codex_home,
                system_config,
            )
            is None
        )
        child_agent_id = f"agent-{depth + 1}"
        _run(
            _post_payload(
                project,
                tool_use_id=tool_use_id,
                child_agent_id=child_agent_id,
                agent_id=parent_agent_id,
            ),
            codex_home,
            system_config,
        )
        assert _agent_depth(codex_home, child_agent_id) == depth + 1
        parent_agent_id = child_agent_id

    blocked = _run(
        _pre_payload(project, tool_use_id="blocked", agent_id=parent_agent_id),
        codex_home,
        system_config,
    )
    assert blocked is not None
    assert blocked["decision"] == "block"
    assert f"agents.max_depth={max_depth}" in blocked["reason"]
    assert f"caller_depth={max_depth}" in blocked["reason"]


def test_post_tool_use_accepts_json_string_tool_response(tmp_path) -> None:
    codex_home = _codex_home(tmp_path)
    project = tmp_path / "project"
    project.mkdir()
    system_config = _system_config(tmp_path)
    _write_user_depth(codex_home, 2)
    initialize_subagent_depth_state(codex_home)

    assert _run(_pre_payload(project), codex_home, system_config) is None
    _run(
        _post_payload(project, child_agent_id="child", response_as_string=True),
        codex_home,
        system_config,
    )

    assert _agent_depth(codex_home, "child") == 1


def test_unknown_agent_blocks_when_policy_is_active(tmp_path) -> None:
    codex_home = _codex_home(tmp_path)
    project = tmp_path / "project"
    project.mkdir()
    system_config = _system_config(tmp_path)
    _write_user_depth(codex_home, 3)
    initialize_subagent_depth_state(codex_home)

    blocked = _run(
        _pre_payload(project, tool_use_id="child-spawn", agent_id="missing-agent"),
        codex_home,
        system_config,
    )

    assert blocked is not None
    assert "could not verify Codex subagent depth" in blocked["reason"]


def test_unknown_agent_does_not_block_when_policy_is_dormant(tmp_path) -> None:
    codex_home = _codex_home(tmp_path)
    project = tmp_path / "project"
    project.mkdir()
    system_config = _system_config(tmp_path)
    initialize_subagent_depth_state(codex_home)

    assert (
        _run(
            _pre_payload(project, tool_use_id="child-spawn", agent_id="missing-agent"),
            codex_home,
            system_config,
        )
        is None
    )


def test_child_pre_waits_for_parent_post_correlation(tmp_path) -> None:
    codex_home = _codex_home(tmp_path)
    project = tmp_path / "project"
    project.mkdir()
    system_config = _system_config(tmp_path)
    _write_user_depth(codex_home, 2)
    initialize_subagent_depth_state(codex_home)

    assert _run(_pre_payload(project, tool_use_id="root-spawn"), codex_home, system_config) is None
    result: list[dict | None] = []

    def child_pre() -> None:
        result.append(
            _run(
                _pre_payload(project, tool_use_id="child-spawn", agent_id="child"),
                codex_home,
                system_config,
                wait=0.5,
            )
        )

    thread = threading.Thread(target=child_pre)
    thread.start()
    time.sleep(0.05)
    _run(
        _post_payload(project, tool_use_id="root-spawn", child_agent_id="child"),
        codex_home,
        system_config,
    )
    thread.join(timeout=2)

    assert result == [None]


def test_live_numeric_change_applies_on_next_spawn(tmp_path) -> None:
    codex_home = _codex_home(tmp_path)
    project = tmp_path / "project"
    project.mkdir()
    system_config = _system_config(tmp_path)
    _write_user_depth(codex_home, 1)
    initialize_subagent_depth_state(codex_home)

    assert _run(_session_payload(project), codex_home, system_config) is None
    assert _run(_pre_payload(project, tool_use_id="root"), codex_home, system_config) is None
    _run(_post_payload(project, tool_use_id="root", child_agent_id="child"), codex_home, system_config)

    _write_user_depth(codex_home, 2)
    assert (
        _run(
            _pre_payload(project, tool_use_id="grandchild", agent_id="child"),
            codex_home,
            system_config,
        )
        is None
    )
    _run(
        _post_payload(
            project,
            tool_use_id="grandchild",
            child_agent_id="grandchild",
            agent_id="child",
        ),
        codex_home,
        system_config,
    )

    blocked = _run(
        _pre_payload(project, tool_use_id="blocked", agent_id="grandchild"),
        codex_home,
        system_config,
    )
    assert blocked is not None
    assert "agents.max_depth=2" in blocked["reason"]


def test_removed_key_stays_latched_until_session_start(tmp_path) -> None:
    codex_home = _codex_home(tmp_path)
    project = tmp_path / "project"
    project.mkdir()
    system_config = _system_config(tmp_path)
    _write_user_depth(codex_home, 1)
    initialize_subagent_depth_state(codex_home)

    assert _run(_session_payload(project), codex_home, system_config) is None
    assert _run(_pre_payload(project, tool_use_id="root"), codex_home, system_config) is None
    _run(_post_payload(project, tool_use_id="root", child_agent_id="child"), codex_home, system_config)

    _write_user_depth(codex_home, None)
    blocked = _run(
        _pre_payload(project, tool_use_id="still-latched", agent_id="child"),
        codex_home,
        system_config,
    )
    assert blocked is not None
    assert "agents.max_depth=1" in blocked["reason"]

    assert _run(_session_payload(project), codex_home, system_config) is None
    assert (
        _run(
            _pre_payload(project, tool_use_id="cleared", agent_id="child"),
            codex_home,
            system_config,
        )
        is None
    )


def test_malformed_config_fails_closed_for_spawn(tmp_path) -> None:
    codex_home = _codex_home(tmp_path)
    project = tmp_path / "project"
    project.mkdir()
    system_config = _system_config(tmp_path)
    codex_home.joinpath("config.toml").write_text("[agents\n", encoding="utf-8")
    initialize_subagent_depth_state(codex_home)

    blocked = _run(_pre_payload(project), codex_home, system_config)

    assert blocked is not None
    assert "could not resolve agents.max_depth" in blocked["reason"]


def test_project_config_overrides_user_only_when_trusted(tmp_path) -> None:
    codex_home = _codex_home(tmp_path)
    project = tmp_path / "project"
    project.joinpath(".codex").mkdir(parents=True)
    project.joinpath(".codex", "config.toml").write_text(
        "[agents]\nmax_depth = 3\n",
        encoding="utf-8",
    )
    _write_user_depth(codex_home, 2)
    system_config = _system_config(tmp_path)

    assert (
        resolve_effective_max_depth(project, codex_home=codex_home, system_config_path=system_config)
        .max_depth
        == 2
    )

    codex_home.joinpath("config.toml").write_text(
        f"""
[agents]
max_depth = 2

[projects."{project}"]
trust_level = "trusted"
""",
        encoding="utf-8",
    )

    assert (
        resolve_effective_max_depth(project, codex_home=codex_home, system_config_path=system_config)
        .max_depth
        == 3
    )


def test_nested_project_config_nearest_value_wins(tmp_path) -> None:
    codex_home = _codex_home(tmp_path)
    root = tmp_path / "repo"
    child = root / "child"
    root.joinpath(".git").mkdir(parents=True)
    root.joinpath(".codex").mkdir()
    child.joinpath(".codex").mkdir(parents=True)
    root.joinpath(".codex", "config.toml").write_text("[agents]\nmax_depth = 4\n", encoding="utf-8")
    child.joinpath(".codex", "config.toml").write_text("[agents]\nmax_depth = 1\n", encoding="utf-8")
    codex_home.joinpath("config.toml").write_text(
        f'[projects."{root}"]\ntrust_level = "trusted"\n',
        encoding="utf-8",
    )

    assert resolve_effective_max_depth(child, codex_home=codex_home).max_depth == 1


def test_project_trust_uses_canonical_symlink_key(tmp_path) -> None:
    codex_home = _codex_home(tmp_path)
    real_project = tmp_path / "real"
    real_project.joinpath(".codex").mkdir(parents=True)
    real_project.joinpath(".codex", "config.toml").write_text(
        "[agents]\nmax_depth = 1\n",
        encoding="utf-8",
    )
    linked_project = tmp_path / "link"
    linked_project.symlink_to(real_project, target_is_directory=True)
    codex_home.joinpath("config.toml").write_text(
        f'[projects."{real_project}"]\ntrust_level = "trusted"\n',
        encoding="utf-8",
    )

    assert resolve_effective_max_depth(linked_project, codex_home=codex_home).max_depth == 1


def test_linked_worktree_can_use_repo_root_trust(tmp_path) -> None:
    codex_home = _codex_home(tmp_path)
    repo = tmp_path / "repo"
    worktree = tmp_path / "worktree"
    repo_git = repo / ".git"
    worktree_git = repo_git / "worktrees" / "worktree"
    worktree_git.mkdir(parents=True)
    worktree.mkdir()
    worktree.joinpath(".git").write_text(f"gitdir: {worktree_git}\n", encoding="utf-8")
    worktree_git.joinpath("commondir").write_text("../..\n", encoding="utf-8")
    worktree.joinpath(".codex").mkdir()
    worktree.joinpath(".codex", "config.toml").write_text(
        "[agents]\nmax_depth = 2\n",
        encoding="utf-8",
    )
    codex_home.joinpath("config.toml").write_text(
        f'[projects."{repo}"]\ntrust_level = "trusted"\n',
        encoding="utf-8",
    )

    assert resolve_effective_max_depth(worktree, codex_home=codex_home).max_depth == 2

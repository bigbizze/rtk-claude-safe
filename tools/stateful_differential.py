"""Compare Go and Python depth-hook state transitions in temporary homes."""

from __future__ import annotations

import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tests" / "reference"))
from rtk_claude_safe.codex_subagent_depth import handle_payload


def state(home: Path) -> dict:
    with sqlite3.connect(home / "rtk-claude-safe" / "subagent-depth.sqlite") as database:
        return {
            "sessions": database.execute("SELECT session_id,max_depth,policy_error FROM sessions ORDER BY session_id").fetchall(),
            "agents": database.execute("SELECT agent_id,session_id,depth,agent_type,parent_agent_id,parent_tool_use_id FROM agents ORDER BY agent_id").fetchall(),
            "pending": database.execute("SELECT session_id,tool_use_id,parent_agent_id,parent_depth FROM pending_spawns ORDER BY session_id,tool_use_id").fetchall(),
            "completed": database.execute("SELECT session_id,tool_use_id,child_agent_id,parent_agent_id,child_depth FROM completed_spawns ORDER BY session_id,tool_use_id").fetchall(),
        }


def main() -> None:
    count = 0
    with tempfile.TemporaryDirectory(prefix="rtk-depth-equivalence-") as temporary:
        root = Path(temporary)
        binary = root / "rtk-claude-safe"
        subprocess.run(["go", "build", "-o", str(binary), "."], cwd=ROOT, check=True)
        for maximum in range(1, 5):
            python_home, go_home = root / f"python-{maximum}", root / f"go-{maximum}"
            for home in (python_home, go_home):
                home.mkdir()
                (home / "config.toml").write_text(f"[agents]\nmax_depth = {maximum}\n")

            def compare(payload: dict) -> None:
                nonlocal count
                expected = handle_payload(payload, codex_home=python_home, unknown_agent_wait_seconds=0)
                result = subprocess.run(
                    [str(binary), "subagent-depth-hook"], input=json.dumps(payload),
                    env={**os.environ, "CODEX_HOME": str(go_home)}, text=True,
                    capture_output=True, timeout=10, check=True,
                )
                actual = json.loads(result.stdout) if result.stdout else None
                assert actual == expected, (payload, expected, actual)
                assert state(python_home) == state(go_home), (payload, state(python_home), state(go_home))
                count += 1

            def payload(event: str, parent: str, tool: str) -> dict:
                return {"hook_event_name": event, "session_id": "session", "cwd": str(root),
                        "tool_name": "Agent", "agent_id": parent, "tool_use_id": tool,
                        "tool_input": {"agent_type": "worker"}}

            compare(payload("SessionStart", "", ""))
            parent = ""
            for level in range(maximum + 1):
                tool = f"tool-{level}"
                compare(payload("PreToolUse", parent, tool))
                if level == maximum:
                    break
                response = payload("PostToolUse", parent, tool)
                response["tool_response"] = json.dumps({"content": [{"text": json.dumps({"agent_id": f"child-{level}"})}]})
                compare(response)
                compare(response)
                parent = f"child-{level}"
            for home in (python_home, go_home):
                (home / "config.toml").write_text("")
            compare(payload("PreToolUse", parent, "latched"))
            compare(payload("SessionStart", "", ""))
            compare(payload("PreToolUse", parent, "dormant"))
            for home in (python_home, go_home):
                (home / "config.toml").write_text(f"[agents]\nmax_depth = {maximum + 1}\n")
            compare(payload("PreToolUse", parent, "updated"))
    print(f"Python/Go depth equivalence: {count} events and complete logical SQLite state matched.")


if __name__ == "__main__":
    main()

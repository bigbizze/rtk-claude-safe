from __future__ import annotations

import subprocess
import sys

import pytest


@pytest.mark.parametrize("command", ["claude-hook", "codex-hook"])
def test_hook_does_not_import_management_modules(command: str) -> None:
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "import sys; from rtk_claude_safe.cli import main; "
            "assert main([sys.argv[1]]) == 0; "
            "assert not {'rtk_claude_safe.management_cli', "
            "'rtk_claude_safe.installer', 'rtk_claude_safe.codex_sqlite', "
            "'rtk_claude_safe.codex_subagent_depth', 'sqlite3', 'urllib.request'} "
            ".intersection(sys.modules)",
            command,
        ],
        input="{}",
        text=True,
        capture_output=True,
        timeout=10,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout == ""

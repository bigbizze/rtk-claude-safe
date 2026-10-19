from __future__ import annotations

import shlex
import subprocess
import sys

from rtk_claude_safe.hook_command import build_hook_command


def test_build_hook_command_prefers_active_absolute_console_script(monkeypatch, tmp_path) -> None:
    current = tmp_path / "current" / "bin" / "rtk-claude-safe"
    old = tmp_path / "old" / "bin" / "rtk-claude-safe"
    current.parent.mkdir(parents=True)
    old.parent.mkdir(parents=True)
    current.touch()
    old.touch()

    monkeypatch.setattr(sys, "argv", [str(current), "init"])
    monkeypatch.setenv("PATH", str(old.parent))

    assert build_hook_command("codex-hook") == f"{shlex.quote(str(current.resolve()))} codex-hook"


def test_build_hook_command_uses_module_fallback_for_python_m(monkeypatch, tmp_path) -> None:
    old = tmp_path / "old" / "bin" / "rtk-claude-safe"
    module_main = tmp_path / "src" / "rtk_claude_safe" / "__main__.py"
    python = tmp_path / "venv" / "bin" / "python"
    old.parent.mkdir(parents=True)
    module_main.parent.mkdir(parents=True)
    python.parent.mkdir(parents=True)
    old.touch()
    module_main.touch()
    python.touch()

    monkeypatch.setattr(sys, "argv", [str(module_main), "init"])
    monkeypatch.setattr(sys, "executable", str(python))
    monkeypatch.setenv("PATH", str(old.parent))

    assert (
        build_hook_command("claude-hook")
        == f"{shlex.quote(str(python))} -m rtk_claude_safe claude-hook"
    )


def test_build_hook_command_quotes_windows_console_script(monkeypatch) -> None:
    monkeypatch.setattr("rtk_claude_safe.hook_command.platform.system", lambda: "Windows")

    script = r"C:\Program Files\rtk-claude-safe\rtk-claude-safe.exe"

    assert build_hook_command("codex-hook", script=script) == subprocess.list2cmdline(
        [script, "codex-hook"]
    )

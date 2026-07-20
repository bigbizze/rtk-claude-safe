from __future__ import annotations

from pathlib import Path

from rtk_claude_safe import cli
from rtk_claude_safe.codex_sqlite import VacuumResult


def _patch_cli_paths(monkeypatch, tmp_path: Path) -> tuple[Path, Path]:
    claude_settings = tmp_path / ".claude" / "settings.json"
    codex_hooks = tmp_path / ".codex" / "hooks.json"
    monkeypatch.setattr(cli, "DEFAULT_SETTINGS_PATH", claude_settings)
    monkeypatch.setattr(cli, "DEFAULT_CODEX_HOOKS_PATH", codex_hooks)
    monkeypatch.setattr(cli, "DEFAULT_CODEX_CONFIG_PATH", tmp_path / ".codex" / "config.toml")
    return claude_settings, codex_hooks


def _patch_cli_actions(monkeypatch) -> list[tuple[str, object]]:
    calls: list[tuple[str, object]] = []

    def ensure_rtk(install_dir: Path) -> Path:
        calls.append(("ensure", install_dir))
        return Path("/bin/rtk")

    def patch_settings(settings_path: Path) -> bool:
        calls.append(("claude", settings_path))
        return True

    def patch_codex_hooks(hooks_path: Path) -> bool:
        calls.append(("codex", hooks_path))
        return True

    monkeypatch.setattr(cli, "ensure_rtk", ensure_rtk)
    monkeypatch.setattr(cli, "patch_settings", patch_settings)
    monkeypatch.setattr(cli, "patch_codex_hooks", patch_codex_hooks)
    monkeypatch.setattr(cli, "inspect_codex_config", lambda _path: [])
    return calls


def test_cli_patches_claude_only(monkeypatch, tmp_path) -> None:
    claude_settings, _codex_hooks = _patch_cli_paths(monkeypatch, tmp_path)
    calls = _patch_cli_actions(monkeypatch)
    claude_settings.parent.mkdir()

    assert cli.main(["init"]) == 0

    assert [name for name, _value in calls] == ["ensure", "claude"]


def test_cli_patches_codex_only(monkeypatch, tmp_path) -> None:
    _claude_settings, codex_hooks = _patch_cli_paths(monkeypatch, tmp_path)
    calls = _patch_cli_actions(monkeypatch)
    codex_hooks.parent.mkdir()

    assert cli.main(["init"]) == 0

    assert [name for name, _value in calls] == ["ensure", "codex"]


def test_cli_skips_codex_on_native_windows(monkeypatch, tmp_path, capsys) -> None:
    _claude_settings, codex_hooks = _patch_cli_paths(monkeypatch, tmp_path)
    calls = _patch_cli_actions(monkeypatch)
    codex_hooks.parent.mkdir()
    monkeypatch.setattr(cli.platform, "system", lambda: "Windows")

    assert cli.main(["init"]) == 0

    assert calls == []
    assert "native Windows Codex hooks are not supported" in capsys.readouterr().err


def test_cli_patches_both(monkeypatch, tmp_path) -> None:
    claude_settings, codex_hooks = _patch_cli_paths(monkeypatch, tmp_path)
    calls = _patch_cli_actions(monkeypatch)
    claude_settings.parent.mkdir()
    codex_hooks.parent.mkdir()

    assert cli.main(["init"]) == 0

    assert [name for name, _value in calls] == ["ensure", "claude", "codex"]


def test_cli_patches_neither_without_installing_rtk(monkeypatch, tmp_path) -> None:
    _patch_cli_paths(monkeypatch, tmp_path)
    calls = _patch_cli_actions(monkeypatch)

    assert cli.main(["init"]) == 0

    assert calls == []


def test_cli_explicit_settings_targets_claude(monkeypatch, tmp_path) -> None:
    _patch_cli_paths(monkeypatch, tmp_path)
    calls = _patch_cli_actions(monkeypatch)
    explicit_settings = tmp_path / "custom" / "settings.json"

    assert cli.main(["init", "--settings", str(explicit_settings)]) == 0

    assert [name for name, _value in calls] == ["ensure", "claude"]
    assert calls[-1] == ("claude", explicit_settings)


def test_cli_rolls_back_all_targets_when_codex_patch_fails(monkeypatch, tmp_path, capsys) -> None:
    claude_settings, codex_hooks = _patch_cli_paths(monkeypatch, tmp_path)
    claude_settings.parent.mkdir()
    codex_hooks.parent.mkdir()
    claude_settings.write_text('{"old":"claude"}\n', encoding="utf-8")
    codex_hooks.write_text('{"old":"codex"}\n', encoding="utf-8")

    monkeypatch.setattr(cli, "ensure_rtk", lambda _install_dir: Path("/bin/rtk"))

    def patch_settings(settings_path: Path) -> bool:
        settings_path.write_text('{"new":"claude"}\n', encoding="utf-8")
        return True

    def patch_codex_hooks(hooks_path: Path) -> bool:
        hooks_path.write_text('{"new":"codex"}\n', encoding="utf-8")
        raise OSError("codex failed")

    monkeypatch.setattr(cli, "patch_settings", patch_settings)
    monkeypatch.setattr(cli, "patch_codex_hooks", patch_codex_hooks)

    assert cli.main(["init"]) == 1

    assert claude_settings.read_text(encoding="utf-8") == '{"old":"claude"}\n'
    assert codex_hooks.read_text(encoding="utf-8") == '{"old":"codex"}\n'
    captured = capsys.readouterr()
    assert "patched scoped Claude" not in captured.out


def test_cli_explicit_settings_does_not_touch_default_settings(monkeypatch, tmp_path) -> None:
    claude_settings, codex_hooks = _patch_cli_paths(monkeypatch, tmp_path)
    codex_hooks.parent.mkdir()
    explicit_settings = tmp_path / "custom" / "settings.json"
    calls: list[tuple[str, Path]] = []

    monkeypatch.setattr(cli, "ensure_rtk", lambda _install_dir: Path("/bin/rtk"))

    def patch_settings(settings_path: Path) -> bool:
        calls.append(("claude", settings_path))
        settings_path.parent.mkdir(parents=True, exist_ok=True)
        settings_path.write_text("{}", encoding="utf-8")
        return True

    def patch_codex_hooks(hooks_path: Path) -> bool:
        calls.append(("codex", hooks_path))
        return False

    monkeypatch.setattr(cli, "patch_settings", patch_settings)
    monkeypatch.setattr(cli, "patch_codex_hooks", patch_codex_hooks)
    monkeypatch.setattr(cli, "inspect_codex_config", lambda _path: [])

    assert cli.main(["init", "--settings", str(explicit_settings)]) == 0

    assert ("claude", explicit_settings) in calls
    assert ("codex", codex_hooks) in calls
    assert not claude_settings.exists()


def test_codex_hook_is_hidden_from_help() -> None:
    assert "codex-hook" not in cli.build_parser().format_help()
    assert "claude-hook" not in cli.build_parser().format_help()
    assert "subagent-depth-hook" not in cli.build_parser().format_help()


def test_enforce_subagent_depth_command_installs_hooks(monkeypatch, tmp_path, capsys) -> None:
    codex_home = tmp_path / ".codex"
    monkeypatch.setenv("CODEX_HOME", str(codex_home))
    monkeypatch.setattr(cli.platform, "system", lambda: "Linux")
    monkeypatch.setattr(cli, "inspect_codex_config", lambda _path: [])
    monkeypatch.setattr(cli, "assert_supported_codex_cli", lambda: "codex-cli 0.144.6")

    assert cli.main(["enforce-subagent-depth"]) == 0

    hooks_path = codex_home / "hooks.json"
    assert hooks_path.exists()
    assert (codex_home / "rtk-claude-safe" / "subagent-depth.sqlite").exists()
    assert not (codex_home / "rtk-claude-safe" / "subagent-depth.disabled").exists()
    assert "configured, pending activation" in capsys.readouterr().out


def test_enforce_subagent_depth_keeps_disabled_marker_when_hook_patch_fails(
    monkeypatch,
    tmp_path,
    capsys,
) -> None:
    codex_home = tmp_path / ".codex"
    marker = codex_home / "rtk-claude-safe" / "subagent-depth.disabled"
    marker.parent.mkdir(parents=True)
    marker.write_text("disabled\n", encoding="utf-8")
    hooks_path = codex_home / "hooks.json"
    hooks_path.write_text('{"old":true}\n', encoding="utf-8")

    monkeypatch.setenv("CODEX_HOME", str(codex_home))
    monkeypatch.setattr(cli.platform, "system", lambda: "Linux")
    monkeypatch.setattr(cli, "assert_supported_codex_cli", lambda: "codex-cli 0.144.6")

    def fail_patch(path: Path) -> bool:
        path.write_text('{"new":true}\n', encoding="utf-8")
        raise OSError("patch failed")

    monkeypatch.setattr(cli, "patch_subagent_depth_hooks", fail_patch)

    assert cli.main(["enforce-subagent-depth"]) == 1

    assert marker.exists()
    assert marker.read_text(encoding="utf-8") == "disabled\n"
    assert hooks_path.read_text(encoding="utf-8") == '{"old":true}\n'
    assert "patch failed" in capsys.readouterr().err


def test_enforce_subagent_depth_reports_sqlite_initialization_failure(
    monkeypatch,
    tmp_path,
    capsys,
) -> None:
    codex_home = tmp_path / ".codex"
    monkeypatch.setenv("CODEX_HOME", str(codex_home))
    monkeypatch.setattr(cli.platform, "system", lambda: "Linux")
    monkeypatch.setattr(cli, "assert_supported_codex_cli", lambda: "codex-cli 0.144.6")

    def fail_initialize(_codex_home: Path, *, enable: bool) -> Path:
        assert enable is False
        raise cli.sqlite3.DatabaseError("bad db")

    monkeypatch.setattr(cli, "initialize_subagent_depth_state", fail_initialize)

    assert cli.main(["enforce-subagent-depth"]) == 1

    assert "bad db" in capsys.readouterr().err


def test_remove_subagent_depth_enforcement_command_disables_and_removes(
    monkeypatch,
    tmp_path,
    capsys,
) -> None:
    codex_home = tmp_path / ".codex"
    monkeypatch.setenv("CODEX_HOME", str(codex_home))
    monkeypatch.setattr(cli.platform, "system", lambda: "Linux")
    monkeypatch.setattr(cli, "inspect_codex_config", lambda _path: [])
    monkeypatch.setattr(cli, "assert_supported_codex_cli", lambda: "codex-cli 0.144.6")

    assert cli.main(["enforce-subagent-depth"]) == 0
    assert cli.main(["remove-subagent-depth-enforcement"]) == 0

    state_dir = codex_home / "rtk-claude-safe"
    assert (state_dir / "subagent-depth.disabled").exists()
    assert not (state_dir / "subagent-depth.sqlite").exists()
    assert "disabled marker retained" in capsys.readouterr().out


def test_enforce_subagent_depth_command_rejects_unsupported_codex(
    monkeypatch,
    tmp_path,
    capsys,
) -> None:
    codex_home = tmp_path / ".codex"
    monkeypatch.setenv("CODEX_HOME", str(codex_home))
    monkeypatch.setattr(cli.platform, "system", lambda: "Linux")

    def unsupported() -> str:
        raise cli.CodexVersionError("unsupported version")

    monkeypatch.setattr(cli, "assert_supported_codex_cli", unsupported)

    assert cli.main(["enforce-subagent-depth"]) == 1

    assert "unsupported version" in capsys.readouterr().err
    assert not codex_home.exists()


def test_repair_codex_sqlite_command_installs_trigger(monkeypatch, tmp_path, capsys) -> None:
    db = tmp_path / "logs_2.sqlite"
    calls: list[tuple[str, object]] = []

    monkeypatch.setattr(cli, "confirm_codex_sessions_closed", lambda: calls.append(("confirm", None)))
    monkeypatch.setattr(cli, "assert_codex_sessions_closed", lambda: calls.append(("assert", None)))

    def install(database: Path) -> bool:
        calls.append(("install", database))
        return True

    monkeypatch.setattr(cli, "install_low_level_log_trigger", install)

    assert cli.main(["repair-codex-sqlite", "--database", str(db)]) == 0

    assert calls == [("confirm", None), ("assert", None), ("install", db)]
    assert "installed Codex SQLite low-level log trigger" in capsys.readouterr().out


def test_repair_codex_sqlite_command_reports_failure(monkeypatch, tmp_path, capsys) -> None:
    db = tmp_path / "logs_2.sqlite"
    monkeypatch.setattr(cli, "confirm_codex_sessions_closed", lambda: None)
    monkeypatch.setattr(cli, "assert_codex_sessions_closed", lambda: None)
    monkeypatch.setattr(
        cli,
        "install_low_level_log_trigger",
        lambda _database: (_ for _ in ()).throw(cli.CodexSqliteError("bad db")),
    )

    assert cli.main(["repair-codex-sqlite", "--database", str(db)]) == 1

    assert "bad db" in capsys.readouterr().err


def test_revert_codex_sqlite_repair_command_removes_trigger(monkeypatch, tmp_path, capsys) -> None:
    db = tmp_path / "logs_2.sqlite"
    calls: list[tuple[str, object]] = []

    monkeypatch.setattr(cli, "confirm_codex_sessions_closed", lambda: calls.append(("confirm", None)))
    monkeypatch.setattr(cli, "assert_codex_sessions_closed", lambda: calls.append(("assert", None)))

    def revert(database: Path) -> bool:
        calls.append(("revert", database))
        return True

    monkeypatch.setattr(cli, "revert_low_level_log_trigger", revert)

    assert cli.main(["revert-codex-sqlite-repair", "--database", str(db)]) == 0

    assert calls == [("confirm", None), ("assert", None), ("revert", db)]
    assert "removed Codex SQLite low-level log trigger" in capsys.readouterr().out


def test_vacuum_codex_sqlite_command_passes_backup_flag(monkeypatch, tmp_path, capsys) -> None:
    db = tmp_path / "logs_2.sqlite"
    backup = tmp_path / "logs_2.sqlite.backup-20260720-103000"
    calls: list[tuple[str, object]] = []

    monkeypatch.setattr(cli, "confirm_codex_sessions_closed", lambda: calls.append(("confirm", None)))
    monkeypatch.setattr(cli, "assert_codex_sessions_closed", lambda: calls.append(("assert", None)))

    def vacuum(database: Path, *, backup: bool) -> object:
        calls.append(("vacuum", (database, backup)))
        return VacuumResult(
            database=database,
            backup=tmp_path / "logs_2.sqlite.backup-20260720-103000" if backup else None,
            before_bytes=1024,
            after_bytes=512,
            wal_before_bytes=256,
            wal_after_bytes=0,
            checkpoint_busy=0,
            checkpoint_log=1,
            checkpointed=1,
        )

    monkeypatch.setattr(cli, "vacuum_codex_logs", vacuum)

    assert cli.main(["vacuum-codex-sqlite", "--database", str(db), "--backup"]) == 0

    assert calls == [("confirm", None), ("assert", None), ("vacuum", (db, True))]
    output = capsys.readouterr().out
    assert "1.0 KiB -> 512 B" in output
    assert str(backup) in output


def test_hidden_codex_hook_subcommand_dispatches(monkeypatch) -> None:
    monkeypatch.setattr(cli, "codex_hook_main", lambda: 0)

    assert cli.main(["codex-hook"]) == 0


def test_hidden_claude_hook_subcommand_dispatches(monkeypatch) -> None:
    monkeypatch.setattr(cli, "claude_hook_main", lambda: 0)

    assert cli.main(["claude-hook"]) == 0


def test_hidden_subagent_depth_hook_subcommand_dispatches(monkeypatch) -> None:
    monkeypatch.setattr(cli, "subagent_depth_hook_main", lambda: 0)

    assert cli.main(["subagent-depth-hook"]) == 0

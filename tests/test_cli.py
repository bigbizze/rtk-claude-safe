from __future__ import annotations

from pathlib import Path

from rtk_claude_safe import cli


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

    def run_rtk_init(rtk_path: Path) -> None:
        calls.append(("rtk-init", rtk_path))

    def patch_settings(settings_path: Path) -> bool:
        calls.append(("claude", settings_path))
        return True

    def patch_codex_hooks(hooks_path: Path) -> bool:
        calls.append(("codex", hooks_path))
        return True

    monkeypatch.setattr(cli, "ensure_rtk", ensure_rtk)
    monkeypatch.setattr(cli, "run_rtk_init", run_rtk_init)
    monkeypatch.setattr(cli, "patch_settings", patch_settings)
    monkeypatch.setattr(cli, "patch_codex_hooks", patch_codex_hooks)
    monkeypatch.setattr(cli, "inspect_codex_config", lambda _path: [])
    return calls


def test_cli_patches_claude_only(monkeypatch, tmp_path) -> None:
    claude_settings, _codex_hooks = _patch_cli_paths(monkeypatch, tmp_path)
    calls = _patch_cli_actions(monkeypatch)
    claude_settings.parent.mkdir()

    assert cli.main(["init"]) == 0

    assert [name for name, _value in calls] == ["ensure", "rtk-init", "claude"]


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

    assert [name for name, _value in calls] == ["ensure", "rtk-init", "claude", "codex"]


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

    assert [name for name, _value in calls] == ["ensure", "rtk-init", "claude"]
    assert calls[-1] == ("claude", explicit_settings)


def test_codex_hook_is_hidden_from_help() -> None:
    assert "codex-hook" not in cli.build_parser().format_help()
    assert "claude-hook" not in cli.build_parser().format_help()


def test_hidden_codex_hook_subcommand_dispatches(monkeypatch) -> None:
    monkeypatch.setattr(cli, "codex_hook_main", lambda: 0)

    assert cli.main(["codex-hook"]) == 0


def test_hidden_claude_hook_subcommand_dispatches(monkeypatch) -> None:
    monkeypatch.setattr(cli, "claude_hook_main", lambda: 0)

    assert cli.main(["claude-hook"]) == 0

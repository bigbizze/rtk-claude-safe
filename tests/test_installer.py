from __future__ import annotations

from pathlib import Path

from rtk_claude_safe import installer


def test_install_warning_mentions_codex_rewrites(monkeypatch, tmp_path, capsys) -> None:
    install_dir = tmp_path / "bin"

    monkeypatch.setattr(installer, "find_rtk", lambda: None)
    monkeypatch.setattr(installer, "_detect_target", lambda: ("x86_64-test", "tar.gz"))
    monkeypatch.setattr(installer, "_latest_version", lambda: "v0.0.0")
    monkeypatch.setattr(installer, "_download", lambda _url: b"archive")
    monkeypatch.setenv("PATH", "")

    def extract(_archive: bytes, _ext: str, dest: Path) -> None:
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text("rtk", encoding="utf-8")

    monkeypatch.setattr(installer, "_extract_rtk_binary", extract)

    assert installer.ensure_rtk(install_dir) == install_dir / "rtk"

    captured = capsys.readouterr()
    assert "`rtk hook claude` hooks" in captured.err
    assert "Codex `rtk <command>` rewrites" in captured.err


def test_install_reuses_existing_install_dir_binary_when_not_on_path(
    monkeypatch, tmp_path, capsys
) -> None:
    install_dir = tmp_path / "bin"
    installed = install_dir / "rtk"
    install_dir.mkdir()
    installed.write_text("rtk", encoding="utf-8")

    monkeypatch.setattr(installer, "find_rtk", lambda: None)
    monkeypatch.setattr(
        installer,
        "_download",
        lambda _url: (_ for _ in ()).throw(AssertionError("should not download")),
    )
    monkeypatch.setenv("PATH", "")

    assert installer.ensure_rtk(install_dir) == installed

    captured = capsys.readouterr()
    assert f"{install_dir} is not on PATH" in captured.err

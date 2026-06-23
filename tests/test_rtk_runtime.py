from __future__ import annotations

import subprocess

from rtk_claude_safe import rtk_runtime
from rtk_claude_safe.rtk_runtime import RtkRuntimeError, parse_rtk_version_output


def test_parse_supported_stable_versions() -> None:
    assert parse_rtk_version_output("rtk 0.42.4\n").version == (0, 42, 4)
    assert parse_rtk_version_output("warning\nrtk v0.42.5\n").version == (0, 42, 5)
    assert parse_rtk_version_output("rtk.exe 0.42.4\n").version == (0, 42, 4)


def test_parse_rejects_advisory_and_conflicting_output() -> None:
    for output in [
        "latest rtk 0.42.4\n",
        "warning: rtk 0.42.4 available\n",
        "unrelated v0.42.4\n",
        "rtk 0.42.4\nrtk 0.42.5\n",
    ]:
        try:
            parse_rtk_version_output(output)
        except RtkRuntimeError:
            pass
        else:
            raise AssertionError(f"unexpectedly parsed {output!r}")


def test_parse_marks_prerelease() -> None:
    assert parse_rtk_version_output("rtk dev-0.43.0-rc.284\n").prerelease
    assert parse_rtk_version_output("rtk 0.42.4-rc.1\n").prerelease
    assert parse_rtk_version_output("rtk v0.42.4-dev.1\n").prerelease


def test_normalize_windows_paths_on_posix_host() -> None:
    left = rtk_runtime._normalize_rtk_path(r"C:\Tools\RTK.EXE", system="Windows")
    right = rtk_runtime._normalize_rtk_path("c:/tools/rtk.exe", system="Windows")
    assert left == right


def test_probe_captures_subprocess_failures(monkeypatch, tmp_path) -> None:
    binary = tmp_path / "rtk"
    binary.write_text("", encoding="utf-8")

    def boom(*_args, **_kwargs):
        raise PermissionError("nope")

    monkeypatch.setattr(subprocess, "run", boom)

    probe = rtk_runtime.probe_rtk(binary)
    assert not probe.supported
    assert "nope" in (probe.reason or "")

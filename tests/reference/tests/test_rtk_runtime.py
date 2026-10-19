from __future__ import annotations

import subprocess

import pytest

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


def _fake_rtk(tmp_path, version_output: str) -> object:
    rtk = tmp_path / "rtk"
    rtk.write_text(f"#!/bin/sh\nprintf '%s\\n' {version_output!r}\n", encoding="utf-8")
    rtk.chmod(0o755)
    return rtk


@pytest.mark.parametrize(
    ("version_output", "reason"),
    [
        ("rtk 0.42.3", "not supported"),
        ("rtk 0.43.0-rc.1", "not supported"),
        ("warning: rtk 0.42.4 available", "could not find"),
    ],
)
def test_probe_enforces_supported_stable_versions(tmp_path, version_output: str, reason: str) -> None:
    rtk = _fake_rtk(tmp_path, version_output)

    probe = rtk_runtime.probe_rtk(rtk)

    assert not probe.supported
    assert reason in (probe.reason or "")


@pytest.mark.parametrize("version_output", ["rtk 0.42.3", "rtk 0.43.0-rc.1", "not rtk"])
def test_require_supported_rtk_rejects_unsupported_outputs(
    tmp_path, version_output: str
) -> None:
    rtk = _fake_rtk(tmp_path, version_output)

    with pytest.raises(RtkRuntimeError, match="requires stable >= 0.42.4"):
        rtk_runtime.require_supported_rtk(rtk)

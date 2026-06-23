"""Shared RTK runtime discovery and version policy."""

from __future__ import annotations

import ntpath
import os
import platform
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

MIN_SUPPORTED_RTK_VERSION = "0.42.4"
RTK_POLICY_UPDATED_FOR = "v0.42.4"
_MIN_VERSION_TUPLE = (0, 42, 4)
_CURRENT_VERSION_RE = re.compile(r"^v?(\d+)\.(\d+)\.(\d+)$")
_PRERELEASE_RE = re.compile(r"^(?:dev-)?v?(\d+)\.(\d+)\.(\d+)-.+$")


class RtkRuntimeError(RuntimeError):
    """Raised when RTK exists but is not usable for the current policy."""


@dataclass(frozen=True)
class RtkVersion:
    raw: str
    version: tuple[int, int, int]
    prerelease: bool = False


@dataclass(frozen=True)
class RtkProbe:
    path: Path
    output: str
    version: RtkVersion | None
    supported: bool
    reason: str | None = None


def _find_rtk_on_path(system: str | None = None) -> Path | None:
    system = platform.system() if system is None else system
    found = shutil.which("rtk")
    if found is None and system == "Windows":
        found = shutil.which("rtk.exe")
    return Path(found) if found else None


def _normalize_rtk_path(path: os.PathLike[str] | str, system: str | None = None) -> str:
    system = platform.system() if system is None else system
    if system == "Windows":
        return ntpath.normcase(ntpath.normpath(ntpath.abspath(os.fspath(path))))
    return str(Path(path).expanduser().resolve())


def parse_rtk_version_output(output: str) -> RtkVersion:
    """Parse `rtk --version` output, accepting only current-version lines."""
    candidates: list[RtkVersion] = []
    for line in output.splitlines():
        tokens = line.strip().split()
        if len(tokens) < 2 or tokens[0] not in {"rtk", "rtk.exe"}:
            continue
        parsed = _parse_version_token(tokens[1])
        if parsed is None:
            raise RtkRuntimeError(f"could not parse RTK version token {tokens[1]!r}")
        candidates.append(parsed)

    unique = {(c.raw, c.version, c.prerelease) for c in candidates}
    if not unique:
        raise RtkRuntimeError("could not find an RTK current-version line")
    if len(unique) > 1:
        raise RtkRuntimeError("conflicting RTK version lines")
    return candidates[0]


def _parse_version_token(token: str) -> RtkVersion | None:
    prerelease = token.startswith("dev-") or "-" in token
    match = _PRERELEASE_RE.match(token) if prerelease else _CURRENT_VERSION_RE.match(token)
    if match is None:
        return None
    return RtkVersion(
        raw=token,
        version=tuple(int(part) for part in match.groups()),
        prerelease=prerelease,
    )


def is_supported_version(version: RtkVersion) -> bool:
    return not version.prerelease and version.version >= _MIN_VERSION_TUPLE


def probe_rtk(path: Path, timeout: float = 1.0) -> RtkProbe:
    """Run a bounded RTK version probe without inheriting stdio."""
    try:
        result = subprocess.run(
            [str(path), "--version"],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return RtkProbe(path=path, output="", version=None, supported=False, reason="timed out")
    except (OSError, subprocess.SubprocessError, UnicodeError) as e:
        return RtkProbe(path=path, output=str(e), version=None, supported=False, reason=str(e))

    output = f"{result.stdout}\n{result.stderr}"
    if result.returncode != 0:
        return RtkProbe(
            path=path,
            output=output,
            version=None,
            supported=False,
            reason=f"`rtk --version` exited with status {result.returncode}",
        )

    try:
        version = parse_rtk_version_output(output)
    except RtkRuntimeError as e:
        return RtkProbe(path=path, output=output, version=None, supported=False, reason=str(e))

    if not is_supported_version(version):
        reason = (
            f"RTK {version.raw} is not supported; "
            f"requires stable >= {MIN_SUPPORTED_RTK_VERSION}"
        )
        return RtkProbe(path=path, output=output, version=version, supported=False, reason=reason)

    return RtkProbe(path=path, output=output, version=version, supported=True)


def require_supported_rtk(path: Path) -> RtkProbe:
    probe = probe_rtk(path)
    if probe.supported:
        return probe
    detail = f" ({probe.reason})" if probe.reason else ""
    raise RtkRuntimeError(
        f"{path} is not a supported RTK binary{detail}; "
        f"requires stable >= {MIN_SUPPORTED_RTK_VERSION}"
    )


def runtime_rtk_supported() -> bool:
    path = _find_rtk_on_path()
    if path is None:
        return False
    return probe_rtk(path).supported

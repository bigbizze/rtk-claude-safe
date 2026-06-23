"""Download, validate, and install the RTK binary for the current platform."""

from __future__ import annotations

import hashlib
import io
import json
import os
import platform
import re
import shutil
import stat
import sys
import tarfile
import urllib.error
import urllib.request
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

from rtk_claude_safe.rtk_runtime import (
    MIN_SUPPORTED_RTK_VERSION,
    RtkRuntimeError,
    _find_rtk_on_path,
    _normalize_rtk_path,
    is_supported_version,
    parse_rtk_version_output,
    require_supported_rtk,
)

GITHUB_REPO = "rtk-ai/rtk"
LATEST_RELEASE_API = f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest"
DEFAULT_INSTALL_DIR = Path.home() / ".local" / "bin"

_SHA256_RE = re.compile(r"^[0-9a-fA-F]{64}$")


class InstallError(RuntimeError):
    pass


@dataclass(frozen=True)
class ReleaseAsset:
    name: str
    url: str
    digest: str


def _detect_target() -> tuple[str, str]:
    """Return (target_triple, archive_extension) for the current host."""
    system = platform.system()
    machine = platform.machine().lower()

    if machine in ("x86_64", "amd64"):
        arch = "x86_64"
    elif machine in ("arm64", "aarch64"):
        arch = "aarch64"
    else:
        raise InstallError(f"Unsupported architecture: {machine}")

    if system == "Linux":
        triple = (
            "x86_64-unknown-linux-musl"
            if arch == "x86_64"
            else "aarch64-unknown-linux-gnu"
        )
        return triple, "tar.gz"
    if system == "Darwin":
        return f"{arch}-apple-darwin", "tar.gz"
    if system == "Windows":
        if arch != "x86_64":
            raise InstallError("Unsupported Windows architecture: arm64")
        return "x86_64-pc-windows-msvc", "zip"
    raise InstallError(f"Unsupported OS: {system}")


def _latest_version() -> str:
    return _fetch_latest_release().get("tag_name", "")


def _fetch_latest_release() -> dict[str, Any]:
    data = _download_json(LATEST_RELEASE_API)
    if not isinstance(data, dict):
        raise InstallError("GitHub latest release response is not an object")

    tag = data.get("tag_name")
    if not isinstance(tag, str):
        raise InstallError("Could not determine latest RTK release tag")
    if data.get("draft") is not False or data.get("prerelease") is not False:
        raise InstallError(f"Latest RTK release {tag} is not a stable release")

    try:
        version = parse_rtk_version_output(f"rtk {tag}\n")
    except RtkRuntimeError as e:
        raise InstallError(f"Latest RTK release tag {tag!r} is not a stable version") from e
    if not is_supported_version(version):
        raise InstallError(
            f"Latest RTK release {tag} is older than required {MIN_SUPPORTED_RTK_VERSION}"
        )
    assets = data.get("assets")
    if not isinstance(assets, list):
        raise InstallError("GitHub latest release response has no assets list")
    return data


def _download_json(url: str) -> Any:
    try:
        raw = _download(url, accept="application/vnd.github+json", timeout=30)
        return json.loads(raw.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError) as e:
        raise InstallError(f"Could not parse GitHub response from {url}: {e}") from e


def _download(url: str, accept: str | None = None, timeout: int = 120) -> bytes:
    headers = {"User-Agent": "rtk-claude-safe"}
    if accept:
        headers["Accept"] = accept
    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.read()
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        raise InstallError(f"Could not download {url}: {e}") from e


def _asset_by_name(release: dict[str, Any], name: str) -> ReleaseAsset:
    assets = release.get("assets")
    if not isinstance(assets, list):
        raise InstallError("GitHub release response has no assets list")
    matches = [asset for asset in assets if isinstance(asset, dict) and asset.get("name") == name]
    if len(matches) != 1:
        raise InstallError(f"Expected exactly one release asset named {name}, found {len(matches)}")
    asset = matches[0]
    url = asset.get("browser_download_url")
    digest = asset.get("digest")
    if not isinstance(url, str):
        raise InstallError(f"Release asset {name} has no download URL")
    if not isinstance(digest, str):
        raise InstallError(f"Release asset {name} has no digest")
    return ReleaseAsset(name=name, url=url, digest=_parse_api_digest(name, digest))


def _parse_api_digest(name: str, digest: str) -> str:
    algorithm, sep, value = digest.partition(":")
    if sep != ":" or algorithm.lower() != "sha256" or not _SHA256_RE.fullmatch(value):
        raise InstallError(f"Release asset {name} has unsupported digest {digest!r}")
    return value.lower()


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _verify_digest(name: str, data: bytes, expected: str) -> None:
    actual = _sha256(data)
    if actual != expected:
        raise InstallError(f"SHA-256 mismatch for {name}: expected {expected}, got {actual}")


def _checksum_for_archive(checksums: bytes, archive_name: str) -> str:
    text = checksums.decode("utf-8", errors="replace")
    matches: list[str] = []
    for line in text.splitlines():
        parts = line.split()
        if len(parts) != 2 or parts[1] != archive_name:
            continue
        if not _SHA256_RE.fullmatch(parts[0]):
            raise InstallError(f"Malformed SHA-256 checksum for {archive_name}")
        matches.append(parts[0].lower())
    if len(matches) != 1:
        raise InstallError(
            f"Expected exactly one checksum entry for {archive_name}, found {len(matches)}"
        )
    return matches[0]


def _download_verified_archive(target: str, ext: str) -> tuple[str, bytes]:
    release = _fetch_latest_release()
    archive_name = f"rtk-{target}.{ext}"
    archive_asset = _asset_by_name(release, archive_name)
    checksum_asset = _asset_by_name(release, "checksums.txt")

    checksums = _download(checksum_asset.url)
    _verify_digest("checksums.txt", checksums, checksum_asset.digest)
    checksum = _checksum_for_archive(checksums, archive_name)
    if checksum != archive_asset.digest:
        raise InstallError(
            f"Release API digest and checksums.txt disagree for {archive_name}"
        )

    archive = _download(archive_asset.url)
    _verify_digest(archive_name, archive, checksum)
    return archive_name, archive


def _is_safe_member_name(name: str, binary_name: str) -> bool:
    return name == binary_name and not Path(name).is_absolute() and ".." not in Path(name).parts


def _extract_rtk_binary(archive_bytes: bytes, ext: str, dest: Path) -> None:
    binary_name = _rtk_binary_name()
    dest.parent.mkdir(parents=True, exist_ok=True)

    if ext == "tar.gz":
        with tarfile.open(fileobj=io.BytesIO(archive_bytes), mode="r:gz") as tf:
            matches = [
                m
                for m in tf.getmembers()
                if _is_safe_member_name(m.name, binary_name)
                and m.isfile()
                and not (m.issym() or m.islnk() or m.isdir())
            ]
            if len(matches) != 1:
                raise InstallError(f"Expected exactly one regular {binary_name} in tarball")
            extracted = tf.extractfile(matches[0])
            if extracted is None:
                raise InstallError("Tarball member is not a regular file")
            with open(dest, "wb") as out:
                shutil.copyfileobj(extracted, out)
    elif ext == "zip":
        with zipfile.ZipFile(io.BytesIO(archive_bytes)) as zf:
            matches = [
                info
                for info in zf.infolist()
                if _is_safe_member_name(info.filename, binary_name)
                and not info.is_dir()
                and not _zipinfo_is_symlink(info)
            ]
            if len(matches) != 1:
                raise InstallError(f"Expected exactly one regular {binary_name} in zip")
            with zf.open(matches[0]) as src, open(dest, "wb") as out:
                shutil.copyfileobj(src, out)
    else:
        raise InstallError(f"Unknown archive extension: {ext}")


def _zipinfo_is_symlink(info: zipfile.ZipInfo) -> bool:
    mode = info.external_attr >> 16
    return stat.S_ISLNK(mode)


def _rtk_binary_name() -> str:
    return "rtk.exe" if platform.system() == "Windows" else "rtk"


def find_rtk() -> Optional[Path]:
    """Return the rtk binary on PATH, if any."""
    return _find_rtk_on_path()


def _require_path_reachable(selected: Path) -> Path:
    found = _find_rtk_on_path()
    if found is None:
        raise InstallError(
            f"{selected.parent} is not on PATH; add it so hooks can execute bare `rtk`."
        )
    if _normalize_rtk_path(found) != _normalize_rtk_path(selected):
        raise InstallError(f"PATH resolves rtk to {found}, not selected binary {selected}")
    try:
        require_supported_rtk(found)
    except RtkRuntimeError as e:
        raise InstallError(str(e)) from e
    return found


def _install_verified_archive(install_dir: Path) -> Path:
    target, ext = _detect_target()
    archive_name, archive_bytes = _download_verified_archive(target, ext)
    binary_name = _rtk_binary_name()
    install_dir.mkdir(parents=True, exist_ok=True)
    dest = install_dir / binary_name
    temp = install_dir / f".{os.getpid()}-{binary_name}"
    if temp.exists():
        temp.unlink()

    try:
        print(f"[rtk-claude-safe] installing {archive_name} -> {dest}", file=sys.stderr)
        _extract_rtk_binary(archive_bytes, ext, temp)
        if platform.system() != "Windows":
            temp.chmod(temp.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
        require_supported_rtk(temp)
        os.replace(temp, dest)
    except (OSError, RtkRuntimeError, InstallError) as e:
        try:
            if temp.exists():
                temp.unlink()
        finally:
            if isinstance(e, InstallError):
                raise
            raise InstallError(f"Could not install RTK: {e}") from e
    return dest


def ensure_rtk(install_dir: Path = DEFAULT_INSTALL_DIR) -> Path:
    """Make sure a supported stable `rtk` is available on PATH."""
    existing = find_rtk()
    if existing:
        try:
            require_supported_rtk(existing)
        except RtkRuntimeError as e:
            raise InstallError(str(e)) from e
        return _require_path_reachable(existing)

    binary_name = _rtk_binary_name()
    installed = install_dir / binary_name
    if installed.is_file():
        try:
            require_supported_rtk(installed)
        except RtkRuntimeError as e:
            raise InstallError(str(e)) from e
        return _require_path_reachable(installed)

    installed = _install_verified_archive(install_dir)
    return _require_path_reachable(installed)


def run_rtk_init(_rtk_path: Path) -> None:
    """Deprecated compatibility shim. Claude settings are patched directly."""
    raise InstallError("rtk init is not used by rtk-claude-safe")

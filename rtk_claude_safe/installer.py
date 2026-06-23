"""Download and install the rtk binary for the current platform.

Mirrors the logic in rtk's own install.sh
(https://raw.githubusercontent.com/rtk-ai/rtk/refs/heads/master/install.sh) so that
`rtk-claude-safe init` works on a machine that doesn't have rtk yet.
"""

from __future__ import annotations

import io
import json
import os
import platform
import shutil
import stat
import subprocess
import sys
import tarfile
import urllib.request
import zipfile
from pathlib import Path
from typing import Optional

GITHUB_REPO = "rtk-ai/rtk"
LATEST_RELEASE_API = f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest"
DEFAULT_INSTALL_DIR = Path.home() / ".local" / "bin"


class InstallError(RuntimeError):
    pass


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
        return "x86_64-pc-windows-msvc", "zip"
    raise InstallError(f"Unsupported OS: {system}")


def _latest_version() -> str:
    req = urllib.request.Request(
        LATEST_RELEASE_API,
        headers={"Accept": "application/vnd.github+json", "User-Agent": "rtk-claude-safe"},
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        data = json.load(resp)
    tag = data.get("tag_name")
    if not tag:
        raise InstallError("Could not determine latest rtk release tag")
    return tag


def _download(url: str) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": "rtk-claude-safe"})
    with urllib.request.urlopen(req, timeout=120) as resp:
        return resp.read()


def _extract_rtk_binary(archive_bytes: bytes, ext: str, dest: Path) -> None:
    binary_name = "rtk.exe" if platform.system() == "Windows" else "rtk"
    dest.parent.mkdir(parents=True, exist_ok=True)

    if ext == "tar.gz":
        with tarfile.open(fileobj=io.BytesIO(archive_bytes), mode="r:gz") as tf:
            member = next(
                (m for m in tf.getmembers() if Path(m.name).name == binary_name),
                None,
            )
            if member is None:
                raise InstallError(f"{binary_name} not found in tarball")
            extracted = tf.extractfile(member)
            if extracted is None:
                raise InstallError("Tarball member is not a regular file")
            with open(dest, "wb") as out:
                shutil.copyfileobj(extracted, out)
    elif ext == "zip":
        with zipfile.ZipFile(io.BytesIO(archive_bytes)) as zf:
            name = next(
                (n for n in zf.namelist() if Path(n).name == binary_name),
                None,
            )
            if name is None:
                raise InstallError(f"{binary_name} not found in zip")
            with zf.open(name) as src, open(dest, "wb") as out:
                shutil.copyfileobj(src, out)
    else:
        raise InstallError(f"Unknown archive extension: {ext}")

    if platform.system() != "Windows":
        dest.chmod(dest.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


def find_rtk() -> Optional[Path]:
    """Return the rtk binary on PATH, if any."""
    found = shutil.which("rtk")
    return Path(found) if found else None


def ensure_rtk(install_dir: Path = DEFAULT_INSTALL_DIR) -> Path:
    """Make sure an `rtk` binary is available; download one if not. Return its path."""
    existing = find_rtk()
    if existing:
        return existing

    target, ext = _detect_target()
    version = _latest_version()
    archive_name = f"rtk-{target}.{ext}"
    url = f"https://github.com/{GITHUB_REPO}/releases/download/{version}/{archive_name}"
    print(f"[rtk-claude-safe] downloading {url}", file=sys.stderr)

    archive_bytes = _download(url)
    binary_name = "rtk.exe" if platform.system() == "Windows" else "rtk"
    dest = install_dir / binary_name
    _extract_rtk_binary(archive_bytes, ext, dest)

    print(f"[rtk-claude-safe] installed rtk {version} -> {dest}", file=sys.stderr)
    if str(install_dir) not in os.environ.get("PATH", "").split(os.pathsep):
        print(
            f"[rtk-claude-safe] note: {install_dir} is not on PATH; add it so the "
            f"`rtk hook claude` hooks and Codex `rtk <command>` rewrites can resolve at runtime.",
            file=sys.stderr,
        )
    return dest


def run_rtk_init(rtk_path: Path) -> None:
    """Run `rtk init -g --hook-only` so rtk lays down its default hook + config."""
    print("[rtk-claude-safe] running `rtk init -g --hook-only`", file=sys.stderr)
    subprocess.run([str(rtk_path), "init", "-g", "--hook-only"], check=True)

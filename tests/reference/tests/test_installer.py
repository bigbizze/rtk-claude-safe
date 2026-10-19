from __future__ import annotations

import io
import hashlib
import stat
import tarfile
import zipfile
from pathlib import Path

import pytest

from rtk_claude_safe import installer
from rtk_claude_safe.rtk_runtime import RtkRuntimeError


def test_ensure_rtk_reuses_supported_path_binary(monkeypatch, tmp_path) -> None:
    rtk = tmp_path / "rtk"
    rtk.write_text("binary", encoding="utf-8")

    monkeypatch.setattr(installer, "find_rtk", lambda: rtk)
    monkeypatch.setattr(installer, "require_supported_rtk", lambda _path: object())
    monkeypatch.setattr(installer, "_require_path_reachable", lambda path: path)

    assert installer.ensure_rtk(tmp_path / "bin") == rtk


def test_ensure_rtk_rejects_stale_path_binary(monkeypatch, tmp_path) -> None:
    rtk = tmp_path / "rtk"
    rtk.write_text("binary", encoding="utf-8")

    def reject(_path: Path) -> object:
        raise RtkRuntimeError("stale")

    monkeypatch.setattr(installer, "find_rtk", lambda: rtk)
    monkeypatch.setattr(installer, "require_supported_rtk", reject)

    with pytest.raises(installer.InstallError, match="stale"):
        installer.ensure_rtk(tmp_path / "bin")


def test_ensure_rtk_fails_when_install_dir_binary_not_on_path(monkeypatch, tmp_path) -> None:
    install_dir = tmp_path / "bin"
    installed = install_dir / "rtk"
    install_dir.mkdir()
    installed.write_text("binary", encoding="utf-8")

    monkeypatch.setattr(installer, "find_rtk", lambda: None)
    monkeypatch.setattr(installer, "_find_rtk_on_path", lambda: None)
    monkeypatch.setattr(installer, "require_supported_rtk", lambda _path: object())

    with pytest.raises(installer.InstallError, match="not on PATH"):
        installer.ensure_rtk(install_dir)


def test_checksum_for_archive_requires_exactly_one_entry() -> None:
    checksum = "a" * 64
    content = f"{checksum}  rtk-x86_64-unknown-linux-musl.tar.gz\n".encode()

    assert installer._checksum_for_archive(content, "rtk-x86_64-unknown-linux-musl.tar.gz") == checksum

    with pytest.raises(installer.InstallError, match="found 0"):
        installer._checksum_for_archive(content, "missing.tar.gz")

    duplicate = content + content
    with pytest.raises(installer.InstallError, match="found 2"):
        installer._checksum_for_archive(duplicate, "rtk-x86_64-unknown-linux-musl.tar.gz")

    malformed = b"not-a-sha  rtk-x86_64-unknown-linux-musl.tar.gz\n"
    with pytest.raises(installer.InstallError, match="Malformed"):
        installer._checksum_for_archive(malformed, "rtk-x86_64-unknown-linux-musl.tar.gz")


def test_extract_tar_requires_single_regular_binary(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(installer, "_rtk_binary_name", lambda: "rtk")
    archive = io.BytesIO()
    with tarfile.open(fileobj=archive, mode="w:gz") as tf:
        data = b"binary"
        info = tarfile.TarInfo("rtk")
        info.size = len(data)
        tf.addfile(info, io.BytesIO(data))

    dest = tmp_path / "rtk"
    installer._extract_rtk_binary(archive.getvalue(), "tar.gz", dest)

    assert dest.read_bytes() == b"binary"


def test_extract_tar_rejects_duplicate_binary(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(installer, "_rtk_binary_name", lambda: "rtk")
    archive = io.BytesIO()
    with tarfile.open(fileobj=archive, mode="w:gz") as tf:
        for _ in range(2):
            data = b"binary"
            info = tarfile.TarInfo("rtk")
            info.size = len(data)
            tf.addfile(info, io.BytesIO(data))

    with pytest.raises(installer.InstallError, match="exactly one"):
        installer._extract_rtk_binary(archive.getvalue(), "tar.gz", tmp_path / "rtk")


def test_extract_zip_rejects_symlink(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(installer, "_rtk_binary_name", lambda: "rtk")
    archive = io.BytesIO()
    with zipfile.ZipFile(archive, "w") as zf:
        info = zipfile.ZipInfo("rtk")
        info.external_attr = (stat.S_IFLNK | 0o777) << 16
        zf.writestr(info, "target")

    with pytest.raises(installer.InstallError, match="exactly one"):
        installer._extract_rtk_binary(archive.getvalue(), "zip", tmp_path / "rtk")


def _asset(name: str, data: bytes, digest: str | None = None) -> dict[str, str]:
    return {
        "name": name,
        "browser_download_url": f"https://example.test/{name}",
        "digest": f"sha256:{digest or hashlib.sha256(data).hexdigest()}",
    }


def test_download_verified_archive_validates_release_assets(monkeypatch) -> None:
    archive_name = "rtk-x86_64-unknown-linux-musl.tar.gz"
    archive = b"archive-bytes"
    archive_digest = hashlib.sha256(archive).hexdigest()
    checksums = f"{archive_digest}  {archive_name}\n".encode()
    release = {
        "tag_name": "v0.42.4",
        "draft": False,
        "prerelease": False,
        "assets": [
            _asset(archive_name, archive),
            _asset("checksums.txt", checksums),
        ],
    }

    monkeypatch.setattr(installer, "_fetch_latest_release", lambda: release)
    monkeypatch.setattr(
        installer,
        "_download",
        lambda url: checksums if url.endswith("checksums.txt") else archive,
    )

    assert installer._download_verified_archive(
        "x86_64-unknown-linux-musl", "tar.gz"
    ) == (archive_name, archive)


def test_download_verified_archive_rejects_missing_digest(monkeypatch) -> None:
    archive_name = "rtk-x86_64-unknown-linux-musl.tar.gz"
    archive = b"archive-bytes"
    release = {
        "tag_name": "v0.42.4",
        "draft": False,
        "prerelease": False,
        "assets": [
            {"name": archive_name, "browser_download_url": "https://example.test/archive"},
            _asset("checksums.txt", b""),
        ],
    }

    monkeypatch.setattr(installer, "_fetch_latest_release", lambda: release)

    with pytest.raises(installer.InstallError, match="has no digest"):
        installer._download_verified_archive("x86_64-unknown-linux-musl", "tar.gz")


def test_download_verified_archive_rejects_checksum_file_digest_mismatch(monkeypatch) -> None:
    archive_name = "rtk-x86_64-unknown-linux-musl.tar.gz"
    archive = b"archive-bytes"
    archive_digest = hashlib.sha256(archive).hexdigest()
    checksums = f"{archive_digest}  {archive_name}\n".encode()
    release = {
        "tag_name": "v0.42.4",
        "draft": False,
        "prerelease": False,
        "assets": [
            _asset(archive_name, archive),
            _asset("checksums.txt", checksums, digest="0" * 64),
        ],
    }

    monkeypatch.setattr(installer, "_fetch_latest_release", lambda: release)
    monkeypatch.setattr(installer, "_download", lambda _url: checksums)

    with pytest.raises(installer.InstallError, match="SHA-256 mismatch for checksums.txt"):
        installer._download_verified_archive("x86_64-unknown-linux-musl", "tar.gz")


def test_download_verified_archive_rejects_archive_digest_disagreement(monkeypatch) -> None:
    archive_name = "rtk-x86_64-unknown-linux-musl.tar.gz"
    archive = b"archive-bytes"
    checksum_digest = "1" * 64
    checksums = f"{checksum_digest}  {archive_name}\n".encode()
    release = {
        "tag_name": "v0.42.4",
        "draft": False,
        "prerelease": False,
        "assets": [
            _asset(archive_name, archive),
            _asset("checksums.txt", checksums),
        ],
    }

    monkeypatch.setattr(installer, "_fetch_latest_release", lambda: release)
    monkeypatch.setattr(installer, "_download", lambda _url: checksums)

    with pytest.raises(installer.InstallError, match="disagree"):
        installer._download_verified_archive("x86_64-unknown-linux-musl", "tar.gz")


def test_download_verified_archive_rejects_archive_payload_digest_mismatch(monkeypatch) -> None:
    archive_name = "rtk-x86_64-unknown-linux-musl.tar.gz"
    expected_archive = b"expected-archive-bytes"
    tampered_archive = b"tampered-archive-bytes"
    archive_digest = hashlib.sha256(expected_archive).hexdigest()
    checksums = f"{archive_digest}  {archive_name}\n".encode()
    release = {
        "tag_name": "v0.42.4",
        "draft": False,
        "prerelease": False,
        "assets": [
            _asset(archive_name, expected_archive),
            _asset("checksums.txt", checksums),
        ],
    }

    monkeypatch.setattr(installer, "_fetch_latest_release", lambda: release)
    monkeypatch.setattr(
        installer,
        "_download",
        lambda url: checksums if url.endswith("checksums.txt") else tampered_archive,
    )

    with pytest.raises(installer.InstallError, match=f"SHA-256 mismatch for {archive_name}"):
        installer._download_verified_archive("x86_64-unknown-linux-musl", "tar.gz")


def test_download_verified_archive_rejects_duplicate_assets(monkeypatch) -> None:
    archive_name = "rtk-x86_64-unknown-linux-musl.tar.gz"
    archive = b"archive-bytes"
    release = {
        "tag_name": "v0.42.4",
        "draft": False,
        "prerelease": False,
        "assets": [
            _asset(archive_name, archive),
            _asset(archive_name, archive),
            _asset("checksums.txt", b""),
        ],
    }

    monkeypatch.setattr(installer, "_fetch_latest_release", lambda: release)

    with pytest.raises(installer.InstallError, match="Expected exactly one release asset"):
        installer._download_verified_archive("x86_64-unknown-linux-musl", "tar.gz")


def test_download_verified_archive_rejects_missing_assets(monkeypatch) -> None:
    release = {
        "tag_name": "v0.42.4",
        "draft": False,
        "prerelease": False,
        "assets": [_asset("checksums.txt", b"")],
    }

    monkeypatch.setattr(installer, "_fetch_latest_release", lambda: release)

    with pytest.raises(installer.InstallError, match="found 0"):
        installer._download_verified_archive("x86_64-unknown-linux-musl", "tar.gz")


def test_detect_target_rejects_windows_arm64(monkeypatch) -> None:
    monkeypatch.setattr(installer.platform, "system", lambda: "Windows")
    monkeypatch.setattr(installer.platform, "machine", lambda: "arm64")

    with pytest.raises(installer.InstallError, match="Unsupported Windows architecture"):
        installer._detect_target()

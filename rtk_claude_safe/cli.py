"""rtk-claude-safe CLI entry point."""

from __future__ import annotations

import argparse
import platform
import sys
import os
from dataclasses import dataclass
from pathlib import Path

from rtk_claude_safe import __version__
from rtk_claude_safe.claude_hook import main as claude_hook_main
from rtk_claude_safe.claude_settings import DEFAULT_SETTINGS_PATH, patch_settings
from rtk_claude_safe.codex_hook import main as codex_hook_main
from rtk_claude_safe.codex_settings import (
    DEFAULT_CODEX_CONFIG_PATH,
    DEFAULT_CODEX_HOOKS_PATH,
    inspect_codex_config,
    patch_codex_hooks,
)
from rtk_claude_safe.installer import (
    DEFAULT_INSTALL_DIR,
    InstallError,
    ensure_rtk,
)


@dataclass
class _FileSnapshot:
    path: Path
    existed: bool
    data: bytes | None
    mode: int | None
    missing_parents: list[Path]


class _ConfigTransaction:
    def __init__(self) -> None:
        self._snapshots: dict[Path, _FileSnapshot] = {}

    def snapshot(self, path: Path) -> None:
        path = path.expanduser()
        if path in self._snapshots:
            return
        missing_parents: list[Path] = []
        parent = path.parent
        while not parent.exists():
            missing_parents.append(parent)
            parent = parent.parent
        if path.exists():
            stat_result = path.stat()
            snapshot = _FileSnapshot(
                path=path,
                existed=True,
                data=path.read_bytes(),
                mode=stat_result.st_mode,
                missing_parents=missing_parents,
            )
        else:
            snapshot = _FileSnapshot(
                path=path,
                existed=False,
                data=None,
                mode=None,
                missing_parents=missing_parents,
            )
        self._snapshots[path] = snapshot

    def rollback(self) -> None:
        for snapshot in reversed(list(self._snapshots.values())):
            if snapshot.existed:
                snapshot.path.parent.mkdir(parents=True, exist_ok=True)
                snapshot.path.write_bytes(snapshot.data or b"")
                if snapshot.mode is not None:
                    os.chmod(snapshot.path, snapshot.mode)
            else:
                try:
                    if snapshot.path.exists():
                        snapshot.path.unlink()
                except IsADirectoryError:
                    pass
            for parent in snapshot.missing_parents:
                try:
                    parent.rmdir()
                except OSError:
                    pass


def _cmd_init(args: argparse.Namespace) -> int:
    install_dir = Path(args.install_dir).expanduser()
    explicit_settings = args.settings is not None
    settings_path = Path(args.settings).expanduser() if explicit_settings else DEFAULT_SETTINGS_PATH
    claude_requested = explicit_settings or DEFAULT_SETTINGS_PATH.parent.exists()
    codex_home_exists = DEFAULT_CODEX_HOOKS_PATH.parent.exists()
    codex_skipped_windows = codex_home_exists and platform.system() == "Windows"
    codex_requested = codex_home_exists and not codex_skipped_windows

    if codex_skipped_windows:
        print(
            "[rtk-claude-safe] warning: skipped Codex; native Windows Codex hooks are not supported",
            file=sys.stderr,
        )

    if not claude_requested and not codex_requested:
        print(
            "[rtk-claude-safe] no supported global agent config folder found "
            "(~/.claude or ~/.codex); nothing to do"
        )
        return 0

    try:
        ensure_rtk(install_dir)
    except InstallError as e:
        print(f"[rtk-claude-safe] failed to install rtk: {e}", file=sys.stderr)
        return 1

    tx = _ConfigTransaction()
    if claude_requested:
        tx.snapshot(settings_path)
    if codex_requested:
        tx.snapshot(DEFAULT_CODEX_HOOKS_PATH)

    messages: list[str] = []
    try:
        if claude_requested:
            changed = patch_settings(settings_path)
            if changed:
                messages.append(f"[rtk-claude-safe] patched scoped Claude hooks into {settings_path}")
            else:
                messages.append(f"[rtk-claude-safe] {settings_path} already has scoped Claude hooks")
        else:
            messages.append("[rtk-claude-safe] skipped Claude; ~/.claude was not found")

        if codex_requested:
            codex_changed = patch_codex_hooks(DEFAULT_CODEX_HOOKS_PATH)
            if codex_changed:
                messages.append(f"[rtk-claude-safe] patched Codex hook into {DEFAULT_CODEX_HOOKS_PATH}")
            else:
                messages.append(f"[rtk-claude-safe] {DEFAULT_CODEX_HOOKS_PATH} already has the Codex hook")

            warnings = inspect_codex_config(DEFAULT_CODEX_CONFIG_PATH)

            messages.append(
                "[rtk-claude-safe] Open Codex CLI, run /hooks, review and trust the "
                "rtk-claude-safe hook."
            )
            messages.append("[rtk-claude-safe] Then ask Codex to run: git status")
            messages.append("[rtk-claude-safe] Expected rewritten command: rtk git status")
        else:
            messages.append("[rtk-claude-safe] skipped Codex; ~/.codex was not found")
    except (ValueError, OSError, UnicodeError) as e:
        tx.rollback()
        print(f"[rtk-claude-safe] failed to patch agent hooks: {e}", file=sys.stderr)
        return 1

    for message in messages:
        print(message)
    if codex_requested:
        for warning in warnings:
            print(f"[rtk-claude-safe] warning: {warning}", file=sys.stderr)

    return 0


def _cmd_codex_hook(_args: argparse.Namespace) -> int:
    return codex_hook_main()


def _cmd_claude_hook(_args: argparse.Namespace) -> int:
    return claude_hook_main()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="rtk-claude-safe",
        description="Install rtk and configure supported agents with scoped safe hooks.",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    init = sub.add_parser(
        "init",
        help="Install rtk and patch global Claude/Codex hooks when their config folders exist.",
    )
    init.add_argument(
        "--install-dir",
        default=str(DEFAULT_INSTALL_DIR),
        help=f"Where to drop the rtk binary if it isn't on PATH (default: {DEFAULT_INSTALL_DIR}).",
    )
    init.add_argument(
        "--settings",
        default=None,
        help=(
            "Explicit Claude settings.json path. When provided, Claude patching runs even if "
            f"~/.claude does not exist (default: {DEFAULT_SETTINGS_PATH} when ~/.claude exists)."
        ),
    )
    init.set_defaults(func=_cmd_init)

    return parser


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if argv and argv[0] == "codex-hook":
        return _cmd_codex_hook(argparse.Namespace())
    if argv and argv[0] == "claude-hook":
        return _cmd_claude_hook(argparse.Namespace())

    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())

"""rtk-claude-safe CLI entry point."""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

from rtk_claude_safe import __version__
from rtk_claude_safe.installer import (
    DEFAULT_INSTALL_DIR,
    InstallError,
    ensure_rtk,
    run_rtk_init,
)
from rtk_claude_safe.settings import DEFAULT_SETTINGS_PATH, patch_settings


def _cmd_init(args: argparse.Namespace) -> int:
    install_dir = Path(args.install_dir).expanduser()
    settings_path = Path(args.settings).expanduser()

    try:
        rtk_path = ensure_rtk(install_dir)
    except InstallError as e:
        print(f"[rtk-claude-safe] failed to install rtk: {e}", file=sys.stderr)
        return 1

    try:
        run_rtk_init(rtk_path)
    except subprocess.CalledProcessError as e:
        print(f"[rtk-claude-safe] `rtk init` exited with status {e.returncode}", file=sys.stderr)
        return e.returncode

    changed = patch_settings(settings_path)
    if changed:
        print(f"[rtk-claude-safe] patched scoped hooks into {settings_path}")
    else:
        print(f"[rtk-claude-safe] {settings_path} already has scoped rtk hooks; nothing to do")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="rtk-claude-safe",
        description="Install rtk and configure Claude Code with scoped PreToolUse hooks.",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    init = sub.add_parser(
        "init",
        help="Install rtk (if missing), run `rtk init -g --hook-only`, and scope its Claude hook.",
    )
    init.add_argument(
        "--install-dir",
        default=str(DEFAULT_INSTALL_DIR),
        help=f"Where to drop the rtk binary if it isn't on PATH (default: {DEFAULT_INSTALL_DIR}).",
    )
    init.add_argument(
        "--settings",
        default=str(DEFAULT_SETTINGS_PATH),
        help=f"Path to Claude settings.json (default: {DEFAULT_SETTINGS_PATH}).",
    )
    init.set_defaults(func=_cmd_init)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())

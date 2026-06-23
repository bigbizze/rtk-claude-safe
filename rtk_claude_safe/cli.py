"""rtk-claude-safe CLI entry point."""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

from rtk_claude_safe import __version__
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
    run_rtk_init,
)


def _cmd_init(args: argparse.Namespace) -> int:
    install_dir = Path(args.install_dir).expanduser()
    explicit_settings = args.settings is not None
    settings_path = Path(args.settings).expanduser() if explicit_settings else DEFAULT_SETTINGS_PATH
    claude_requested = explicit_settings or DEFAULT_SETTINGS_PATH.parent.exists()
    codex_requested = DEFAULT_CODEX_HOOKS_PATH.parent.exists()

    if not claude_requested and not codex_requested:
        print(
            "[rtk-claude-safe] no supported global agent config folder found "
            "(~/.claude or ~/.codex); nothing to do"
        )
        return 0

    try:
        rtk_path = ensure_rtk(install_dir)
    except InstallError as e:
        print(f"[rtk-claude-safe] failed to install rtk: {e}", file=sys.stderr)
        return 1

    if claude_requested:
        try:
            run_rtk_init(rtk_path)
        except subprocess.CalledProcessError as e:
            print(f"[rtk-claude-safe] `rtk init` exited with status {e.returncode}", file=sys.stderr)
            return e.returncode

        try:
            changed = patch_settings(settings_path)
        except ValueError as e:
            print(f"[rtk-claude-safe] failed to patch Claude settings: {e}", file=sys.stderr)
            return 1

        if changed:
            print(f"[rtk-claude-safe] patched scoped Claude hooks into {settings_path}")
        else:
            print(f"[rtk-claude-safe] {settings_path} already has scoped Claude hooks")
    else:
        print("[rtk-claude-safe] skipped Claude; ~/.claude was not found")

    if codex_requested:
        try:
            codex_changed = patch_codex_hooks(DEFAULT_CODEX_HOOKS_PATH)
        except ValueError as e:
            print(f"[rtk-claude-safe] failed to patch Codex hooks: {e}", file=sys.stderr)
            return 1

        if codex_changed:
            print(f"[rtk-claude-safe] patched Codex hook into {DEFAULT_CODEX_HOOKS_PATH}")
        else:
            print(f"[rtk-claude-safe] {DEFAULT_CODEX_HOOKS_PATH} already has the Codex hook")

        for warning in inspect_codex_config(DEFAULT_CODEX_CONFIG_PATH):
            print(f"[rtk-claude-safe] warning: {warning}", file=sys.stderr)

        print(
            "[rtk-claude-safe] Open Codex CLI, run /hooks, review and trust the "
            "rtk-claude-safe hook."
        )
        print("[rtk-claude-safe] Then ask Codex to run: git status")
        print("[rtk-claude-safe] Expected rewritten command: rtk git status")
    else:
        print("[rtk-claude-safe] skipped Codex; ~/.codex was not found")

    return 0


def _cmd_codex_hook(_args: argparse.Namespace) -> int:
    return codex_hook_main()


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

    codex_hook = sub.add_parser("codex-hook", help=argparse.SUPPRESS)
    codex_hook.set_defaults(func=_cmd_codex_hook)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())

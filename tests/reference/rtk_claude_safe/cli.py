"""Dispatch hooks without importing installation and maintenance commands."""

from __future__ import annotations

import sys


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if argv and argv[0] == "claude-hook":
        from rtk_claude_safe.claude_hook import main as hook_main

        return hook_main()
    if argv and argv[0] == "codex-hook":
        from rtk_claude_safe.codex_hook import main as hook_main

        return hook_main()
    if argv and argv[0] == "subagent-depth-hook":
        from rtk_claude_safe.codex_subagent_depth import main as hook_main

        return hook_main()

    from rtk_claude_safe.management_cli import main as management_main

    return management_main(argv)


if __name__ == "__main__":
    raise SystemExit(main())

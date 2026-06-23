"""Compatibility exports for the shared safe command allowlist."""

from __future__ import annotations

from rtk_claude_safe.allowlist import SCOPED_PATTERNS, build_claude_scoped_hooks

build_scoped_hooks = build_claude_scoped_hooks

# v0.4.0 Verification Results

Recorded: 2026-06-23

Branch: `update-rtk-0424-baseline`

## Scope

This change updates the repository's RTK compatibility baseline to stable RTK `v0.42.4` and adds
runtime enforcement:

- `rtk-claude-safe init` fails before config mutation when an existing RTK binary is older than
  `0.42.4`, prerelease, or unparseable.
- Missing RTK still installs from the latest stable GitHub release, but the installer verifies
  release metadata, API digests, checksums, archive shape, binary version, and PATH reachability.
- Hidden Claude and Codex hook entrypoints fail open when runtime RTK is missing or unsupported.
- Claude settings are patched directly by this package; the installer no longer delegates to an
  upstream broad hook installer.

## Automated Checks

Initial implementation checkpoint:

- `uv run --extra dev pytest -q`
  - Result: passed
  - Coverage: 213 tests

Final automated checks, review loop notes, benchmark results, and manual smoke results will be
recorded after the implementation review loop.

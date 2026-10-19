# Go port verification

Recorded: 2026-10-19

## Scope

The runtime is now Go, including command classification, Claude/Codex hook adapters, RTK
installation, hook configuration, Codex depth enforcement, and SQLite maintenance. Python
source from commit `2e8a20f` is retained only under `tests/reference/` for differential testing.
The native binary reports version `0.7.0`. The RTK minimum remains stable `0.42.4`.

## Checks

- `go test ./...`: passed.
- `go test -race ./...`: passed.
- `go vet ./...`: passed.
- `go test ./internal/policy -run '^$' -fuzz FuzzUncertainSyntax -fuzztime=5s -parallel=2`:
  passed, with 146,292 executions in the recorded run.
- `python3 tools/differential.py`: 77,034 comparisons passed across 12,831 distinct command
  inputs, both agents, and supported/unsupported RTK payload handling.
- `python3 tools/stateful_differential.py`: 54 depth-hook events matched Python outputs and
  logical SQLite state, including replay, depth limits, configuration latching, and updates.
- Original Python reference tests: 438 passed.
- Linux amd64 build and Windows amd64/macOS arm64 cross-compilation: passed.
- Native CLI smoke test in an isolated HOME: `init`, repeated `init`, depth enforcement setup,
  and depth removal passed. Repeated initialization preserved settings byte-for-byte.
- `git diff --check`: passed.
- Optional `go test -cover ./...`: could not complete because the selected local toolchain
  reports `go: no such tool "covdata"` for packages without test files. Normal tests and race
  tests pass; no full repository coverage percentage is claimed.

The expanded comparisons detected and corrected differences in Unicode case handling,
Unicode decimal digits, integer underscores, control-character whitespace, and JSON integer
precision. Committed Go golden tests run without Python. These checks are not a proof of
behavior for every possible input.

## Measured startup

50 alternating executions per case on the local Linux machine. Python 3.12 ran the optimized
reference entry point. Go ran the complete native binary. Both used the same installed RTK.

| Hook case | Python median | Go median | Speedup |
| --- | ---: | ---: | ---: |
| `echo hello`, no rewrite | 39.697 ms | 2.371 ms | 16.7x |
| `git status`, rewrite and RTK version probe | 41.690 ms | 3.939 ms | 10.6x |

Reproduce with `python3 tools/benchmark.py --python /path/to/python`. Results depend on the
interpreter, machine load, filesystem cache, and RTK executable.

## Deployment status

Go version 0.7.0 is installed at `~/.local/bin/rtk-claude-safe`, replacing the Python entry-point
symlink. The pipx environment remains available for rollback. Claude and Codex now each use
one managed rewrite hook pointing to the Go binary. Live hook payload checks passed for both
agents; unrelated settings were preserved and repeated `init` left the files byte-identical.
Claude settings, Codex hooks, and Codex config were backed up with suffix
`.before-go-20261019075042`. Codex `config.toml` was unchanged. Before worker startup, Claude's
440 managed entries had already been reduced to one.

No repair, revert, vacuum, or depth installation command was run against live user databases
or depth configuration. Installer download validation used local HTTP fixtures. Windows and
macOS binaries were cross-compiled but not executed on those operating systems.

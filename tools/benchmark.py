"""Measure complete hook processes, including the RTK version subprocess."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import statistics
import subprocess
import sys
import time


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--python", default=sys.executable)
    parser.add_argument("--runs", type=int, default=50)
    args = parser.parse_args()
    if args.runs < 1:
        parser.error("--runs must be positive")
    root = Path(__file__).resolve().parents[1]
    binary = root / "build" / "rtk-claude-safe"
    subprocess.run(["go", "build", "-trimpath", "-o", str(binary), "."], cwd=root, check=True)
    env = {**os.environ, "PYTHONPATH": str(root / "tests" / "reference")}
    commands = {"python": [args.python, "-m", "rtk_claude_safe", "claude-hook"],
                "go": [str(binary), "claude-hook"]}
    for command in ("echo hello", "git status"):
        durations = {name: [] for name in commands}
        output = {}
        payload = json.dumps({"tool_name": "Bash", "tool_input": {"command": command}})
        for _ in range(args.runs):
            for name, invocation in commands.items():
                start = time.perf_counter()
                result = subprocess.run(invocation, input=payload, text=True, capture_output=True,
                                        check=True, env=env, cwd=root, timeout=10)
                durations[name].append((time.perf_counter() - start) * 1000)
                output[name] = json.loads(result.stdout) if result.stdout else None
            assert output["python"] == output["go"], output
        print(json.dumps({"command": command, "runs": args.runs,
                          "rewritten": output["go"] is not None,
                          "median_ms": {name: round(statistics.median(values), 3)
                                        for name, values in durations.items()}}))


if __name__ == "__main__":
    main()

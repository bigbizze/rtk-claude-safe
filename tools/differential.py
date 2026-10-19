"""Compare the Go policy and payload adapters with the frozen Python reference."""

from __future__ import annotations

import ast
import itertools
import json
from pathlib import Path
import random
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
REFERENCE = ROOT / "tests" / "reference"
sys.path.insert(0, str(REFERENCE))

from rtk_claude_safe import claude_hook, codex_hook
from rtk_claude_safe.allowlist import rewrite_command_for_agent


def commands() -> list[str]:
    cases = set()
    for path in (REFERENCE / "tests").glob("test_*.py"):
        for node in ast.walk(ast.parse(path.read_text())):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                if len(node.value) < 2000:
                    cases.add(node.value)
    bases = [
        "git status", "git log --oneline", "git diff --stat", "git diff",
        "npm run test", "pnpm --filter app test", "pytest", "cargo test",
        "go test ./...", "eslint .", "ruff check .", "gh pr view 12", "env",
        "pip list", "wc file", "rtk git status", "curl example.com",
    ]
    prefixes = ["", " ", "\t", "cd app && ", "true; ", "env ", "X=1 ", "command "]
    suffixes = [
        "", " ", " --json", " --watch", " --help", " --version", " --output=x",
        " && git status", " || false", "; npm run test", " | cat", " > output",
        " # comment", " $(echo x)", " $HOME", " `date`", "\n", "\r", "\x00",
        " 'a b'", ' "a b"', " 'unfinished", " \\", " 'a;b'", " '--json'",
    ]
    cases.update(prefix + base + suffix for prefix, base, suffix in itertools.product(prefixes, bases, suffixes))
    for value in ["1", "50", "51", "0", "-1", "+1", "_1", "1_", "1__0", "1_0", "١٠", "１２", " 2 ", "\x1c2\x1f"]:
        cases.add("git log --oneline -n '" + value + "'")
    for command in ["PİP list", "GİT status", "npm run buİld", "\x1cgit status\x1f"]:
        cases.add(command)
    randomizer = random.Random(42741)
    atoms = ["git", "status", "npm", "run", "test", "--json", "--", "&&", "||", ";", "'", '"', "\\", "$", "#", "(", ")", "a b", "é", "", "\t"]
    for _ in range(10000):
        cases.add(" ".join(randomizer.choices(atoms, k=randomizer.randint(1, 12))))
    return sorted(cases)


def main() -> None:
    requests = []
    expected = []
    cases = commands()
    for agent in ("claude", "codex"):
        adapter = claude_hook if agent == "claude" else codex_hook
        for command in cases:
            requests.append({"agent": agent, "command": command})
            expected.append(rewrite_command_for_agent(command, agent))
        payloads = [None, False, 1, "text", [], {}, {"tool_name": "Read"},
                    {"tool_name": "Bash", "tool_input": {"command": "git status", "large_integer": 9007199254740993}}]
        payloads.extend(
            {"tool_name": "Bash", "tool_input": {"command": command, "description": "preserve", "timeout": 42}}
            for command in cases
        )
        payloads.extend({"tool_name": "Bash", "tool_input": value} for value in [None, [], "x", 5])
        for supported in (True, False):
            adapter.runtime_rtk_supported = lambda: supported
            for payload in payloads:
                requests.append({"kind": "payload", "agent": agent, "payload": payload, "supported": supported})
                expected.append(adapter.maybe_rewrite_payload(payload))
    with tempfile.TemporaryDirectory(prefix="rtk-go-differential-") as directory:
        binary = Path(directory) / "oracle"
        subprocess.run(["go", "build", "-o", str(binary), "./tools/oracle"], cwd=ROOT, check=True)
        result = subprocess.run(
            [str(binary)], input="".join(json.dumps(request) + "\n" for request in requests),
            text=True, capture_output=True, check=True, timeout=120,
        )
    actual = [json.loads(line) for line in result.stdout.splitlines()]
    if len(actual) != len(expected):
        raise AssertionError(f"Expected {len(expected)} results, got {len(actual)}")
    failures = [(request, want, got) for request, want, got in zip(requests, expected, actual) if want != got]
    if failures:
        for request, want, got in failures[:20]:
            print(json.dumps({"input": request, "python": want, "go": got}, ensure_ascii=True))
        raise AssertionError(f"{len(failures)} differences across {len(requests)} cases")
    print(f"Python/Go equivalence: {len(requests)} cases passed ({len(cases)} distinct command inputs).")


if __name__ == "__main__":
    main()

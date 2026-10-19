package policy

import (
	"bufio"
	"encoding/json"
	"os"
	"strings"
	"testing"
)

func TestPythonGolden(test *testing.T) {
	file, err := os.Open("testdata/python.jsonl")
	if err != nil {
		test.Fatal(err)
	}
	defer file.Close()
	scanner := bufio.NewScanner(file)
	count := 0
	for scanner.Scan() {
		var item struct {
			Command, Agent string
			Expected       *string
		}
		if err := json.Unmarshal(scanner.Bytes(), &item); err != nil {
			test.Fatal(err)
		}
		actual, ok := Rewrite(item.Command, item.Agent)
		if ok != (item.Expected != nil) || (ok && actual != *item.Expected) {
			test.Errorf("%s %q: got %q/%v, expected %v", item.Agent, item.Command, actual, ok, item.Expected)
		}
		count++
	}
	if err := scanner.Err(); err != nil {
		test.Fatal(err)
	}
	if count < 400 {
		test.Fatalf("golden corpus unexpectedly small: %d", count)
	}
}

func TestPythonTextEdgeCases(test *testing.T) {
	for _, command := range []string{"GİT status", "PİP list", "npm run buİld", "git log --oneline -n _1", "git log --oneline -n 1_", "git log --oneline -n 1__0"} {
		if _, ok := Rewrite(command, "claude"); ok {
			test.Errorf("rewrote Python-rejected input: %q", command)
		}
	}
	for _, command := range []string{"git log --oneline -n ١٠", "git log --oneline -n １２", "git log --oneline -n 1_0", "\x1cgit status\x1f"} {
		if _, ok := Rewrite(command, "claude"); !ok {
			test.Errorf("failed Python-supported input: %q", command)
		}
	}
}

func FuzzUncertainSyntax(fuzz *testing.F) {
	for _, seed := range []string{"git status", "npm run test", "cargo test", "'quoted'"} {
		fuzz.Add(seed)
	}
	fuzz.Fuzz(func(test *testing.T, command string) {
		for _, marker := range []string{"$", "`", "\n"} {
			if result, ok := Rewrite(command+marker, "codex"); ok {
				test.Fatalf("unsafe expansion rewritten: %q", result)
			}
		}
		if result, ok := Rewrite(command, "codex"); ok {
			if !strings.Contains(result, "rtk ") {
				test.Fatalf("rewrite contains no RTK invocation: %q", result)
			}
			if again, ok := Rewrite(result, "codex"); ok {
				test.Fatalf("rewrite is not idempotent: %q -> %q", result, again)
			}
		}
	})
}

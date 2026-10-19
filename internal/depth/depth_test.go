package depth

import (
	"bytes"
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"runtime"
	"strings"
	"testing"
	"time"
)

func writeFile(test *testing.T, path, content string) {
	test.Helper()
	if err := os.MkdirAll(filepath.Dir(path), 0700); err != nil {
		test.Fatal(err)
	}
	if err := os.WriteFile(path, []byte(content), 0600); err != nil {
		test.Fatal(err)
	}
}

func testHome(test *testing.T, limit int) (string, string) {
	test.Helper()
	if runtime.GOOS == "windows" {
		test.Skip("Codex depth hooks are Unix only")
	}
	root := test.TempDir()
	home := filepath.Join(root, "home")
	writeFile(test, filepath.Join(home, "config.toml"), fmt.Sprintf("[agents]\nmax_depth = %d\n", limit))
	return home, filepath.Join(root, "system.toml")
}

func event(name, agent, tool string) map[string]any {
	return map[string]any{"hook_event_name": name, "session_id": "session", "tool_name": "Agent", "tool_use_id": tool, "agent_id": agent}
}

func TestDepthLimitsAndReplay(test *testing.T) {
	for limit := 1; limit <= 4; limit++ {
		home, system := testHome(test, limit)
		agent := ""
		for level := 0; level <= limit; level++ {
			tool := fmt.Sprintf("tool-%d", level)
			result, err := handle(event("PreToolUse", agent, tool), home, system, 0)
			if err != nil {
				test.Fatal(err)
			}
			if level == limit {
				if result == nil {
					test.Fatal("depth limit not enforced")
				}
				continue
			}
			if result != nil {
				test.Fatalf("premature block at %d: %v", level, result)
			}
			child := fmt.Sprintf("child-%d", level)
			payload := event("PostToolUse", agent, tool)
			payload["tool_response"] = fmt.Sprintf(`{"content":[{"text":"{\"agent_id\":\"%s\"}"}]}`, child)
			for replay := 0; replay < 2; replay++ {
				if _, err = handle(payload, home, system, 0); err != nil {
					test.Fatal(err)
				}
			}
			agent = child
		}
	}
}

func TestUnknownAgentAndPolicyLatch(test *testing.T) {
	home, system := testHome(test, 1)
	unknown := event("PreToolUse", "unknown", "tool")
	if result, err := handle(unknown, home, system, 0); err != nil || result == nil {
		test.Fatalf("unknown agent not blocked: %v %v", result, err)
	}
	writeFile(test, filepath.Join(home, "config.toml"), "")
	if result, err := handle(unknown, home, system, 0); err != nil || result == nil {
		test.Fatalf("latched policy lost: %v %v", result, err)
	}
	if _, err := handle(event("SessionStart", "", ""), home, system, 0); err != nil {
		test.Fatal(err)
	}
	if result, err := handle(unknown, home, system, 0); err != nil || result != nil {
		test.Fatalf("dormant policy blocked: %v %v", result, err)
	}
	writeFile(test, filepath.Join(home, "config.toml"), "[agents]\nmax_depth = false\n")
	if result, err := handle(event("PreToolUse", "", "root"), home, system, 0); err != nil || result == nil {
		test.Fatalf("invalid config did not block: %v %v", result, err)
	}
}

func TestCallerWaitsForPostCorrelation(test *testing.T) {
	home, system := testHome(test, 2)
	if _, err := handle(event("PreToolUse", "", "root"), home, system, 0); err != nil {
		test.Fatal(err)
	}
	result := make(chan error, 1)
	go func() {
		output, err := handle(event("PreToolUse", "child", "child-tool"), home, system, time.Second)
		if output != nil {
			err = fmt.Errorf("unexpected block: %v", output)
		}
		result <- err
	}()
	time.Sleep(50 * time.Millisecond)
	payload := event("PostToolUse", "", "root")
	payload["tool_response"] = map[string]any{"agent_id": "child"}
	if _, err := handle(payload, home, system, 0); err != nil {
		test.Fatal(err)
	}
	select {
	case err := <-result:
		if err != nil {
			test.Fatal(err)
		}
	case <-time.After(2 * time.Second):
		test.Fatal("correlation deadlocked")
	}
}

func TestTrustedConfigAndSymlink(test *testing.T) {
	home, system := testHome(test, 2)
	project := filepath.Join(test.TempDir(), "project")
	writeFile(test, filepath.Join(project, ".codex", "config.toml"), "[agents]\nmax_depth = 3\n")
	limit, err := resolve(project, home, system)
	if err != nil || limit == nil || *limit != 2 {
		test.Fatalf("untrusted override: %v %v", limit, err)
	}
	writeFile(test, filepath.Join(home, "config.toml"), fmt.Sprintf("[agents]\nmax_depth=2\n[projects.%q]\ntrust_level='trusted'\n", project))
	link := filepath.Join(test.TempDir(), "link")
	if err = os.Symlink(project, link); err != nil {
		test.Fatal(err)
	}
	limit, err = resolve(link, home, system)
	if err != nil || limit == nil || *limit != 3 {
		test.Fatalf("trusted symlink ignored: %v %v", limit, err)
	}
}

func TestDisableAndRemovalPreserveOtherHooks(test *testing.T) {
	home, system := testHome(test, 1)
	test.Setenv("CODEX_HOME", home)
	hooks := filepath.Join(home, "hooks.json")
	writeFile(test, hooks, `{"hooks":{"PreToolUse":[{"matcher":"^Bash$","hooks":[{"type":"command","command":"rtk-claude-safe codex-hook"}]}]}}`)
	if changed, err := patchHooks(hooks, "rtk-claude-safe subagent-depth-hook", false); err != nil || !changed {
		test.Fatalf("%t %v", changed, err)
	}
	if changed, err := patchHooks(hooks, "rtk-claude-safe subagent-depth-hook", false); err != nil || changed {
		test.Fatalf("not idempotent: %t %v", changed, err)
	}
	if _, err := handle(event("SessionStart", "", ""), home, system, 0); err != nil {
		test.Fatal(err)
	}
	var output bytes.Buffer
	if err := Remove(&output, &output); err != nil {
		test.Fatal(err)
	}
	if _, err := os.Stat(databasePath(home)); !os.IsNotExist(err) {
		test.Fatalf("state retained: %v", err)
	}
	if result, err := handle(event("PreToolUse", "unknown", "tool"), home, system, 0); err != nil || result != nil {
		test.Fatalf("disabled hook did work: %v %v", result, err)
	}
	if _, err := os.Stat(databasePath(home)); !os.IsNotExist(err) {
		test.Fatal("disabled hook recreated database")
	}
	raw, _ := os.ReadFile(hooks)
	if !bytes.Contains(raw, []byte("codex-hook")) || bytes.Contains(raw, []byte("subagent-depth-hook")) {
		test.Fatal(string(raw))
	}
}

func TestRunFailsClosedAndPreservesResponseOrder(test *testing.T) {
	home, _ := testHome(test, 1)
	test.Setenv("CODEX_HOME", home)
	writeFile(test, databasePath(home), "not sqlite")
	raw, _ := json.Marshal(event("PreToolUse", "", "tool"))
	var output bytes.Buffer
	if err := Run(bytes.NewReader(raw), &output); err != nil {
		test.Fatal(err)
	}
	if !strings.Contains(output.String(), "failed closed") {
		test.Fatal(output.String())
	}
	value := json.RawMessage(`{"z":{"agent_id":"first"},"a":{"agent_id":"second"}}`)
	if result := extract(value, "agent_id", 0); result != "first" {
		test.Fatal(result)
	}
}

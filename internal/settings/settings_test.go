package settings

import (
	"bytes"
	"os"
	"path/filepath"
	"reflect"
	"runtime"
	"testing"
)

func TestSingleHookMigrationAndOtherAgentPreservation(test *testing.T) {
	for _, agent := range []string{"claude", "codex"} {
		path := filepath.Join(test.TempDir(), "hooks.json")
		matcher := "Bash"
		patcher := PatchClaude
		if agent == "codex" {
			matcher = "^Bash$"
			patcher = PatchCodex
		}
		userHook := map[string]any{"type": "command", "command": "user-hook"}
		otherHook := map[string]any{"type": "command", "command": "rtk-claude-safe subagent-depth-hook"}
		hooks := []any{userHook, otherHook}
		for index := 0; index < 440; index++ {
			hooks = append(hooks, map[string]any{"type": "command", "command": "/old/rtk-claude-safe " + agent + "-hook", "if": "Bash(old*)"})
		}
		otherGroup := map[string]any{"matcher": "Read", "hooks": []any{userHook}}
		data := map[string]any{"theme": "dark", "hooks": map[string]any{"PreToolUse": []any{map[string]any{"matcher": matcher, "hooks": hooks}, map[string]any{"matcher": matcher, "hooks": hooks}, otherGroup}, "SessionStart": []any{otherGroup}}}
		if err := Write(path, data); err != nil {
			test.Fatal(err)
		}
		command := "rtk-claude-safe " + agent + "-hook"
		if changed, err := patcher(path, command); err != nil || !changed {
			test.Fatalf("%t %v", changed, err)
		}
		if changed, err := patcher(path, command); err != nil || changed {
			test.Fatalf("not idempotent: %t %v", changed, err)
		}
		result, err := Load(path)
		if err != nil {
			test.Fatal(err)
		}
		groups := result["hooks"].(map[string]any)["PreToolUse"].([]any)
		count := 0
		for _, value := range groups {
			for _, hook := range value.(map[string]any)["hooks"].([]any) {
				if IsManaged(hook, agent) {
					count++
					if _, exists := hook.(map[string]any)["if"]; exists {
						test.Fatal("condition retained")
					}
				}
			}
		}
		if count != 1 || result["theme"] != "dark" || !reflect.DeepEqual(groups[2], otherGroup) {
			test.Fatalf("wrong migration: %v", result)
		}
		if len(groups[1].(map[string]any)["hooks"].([]any)) != 2 {
			test.Fatal("unrelated hooks removed")
		}
	}
}

func TestManagedCommands(test *testing.T) {
	for _, command := range []string{"rtk hook claude", `"/opt/RTK Dir/rtk" hook claude`, "env FOO=bar rtk hook claude", "rtk-claude-safe claude-hook", `C:\Tools\rtk-claude-safe.exe claude-hook`, "python3.11 -m rtk_claude_safe claude-hook"} {
		if !Managed(command, "claude") {
			test.Errorf("not recognized: %s", command)
		}
	}
	for _, command := range []string{"echo rtk-claude-safe claude-hook", "rtk-claude-safe codex-hook", "rtk-claude-safe claude-hook && echo x"} {
		if Managed(command, "claude") {
			test.Errorf("incorrectly recognized: %s", command)
		}
	}
}

func TestInitRollback(test *testing.T) {
	if runtime.GOOS == "windows" {
		test.Skip("Unix executable fixture")
	}
	home := test.TempDir()
	test.Setenv("HOME", home)
	bin := filepath.Join(home, "bin")
	os.MkdirAll(bin, 0755)
	if err := os.WriteFile(filepath.Join(bin, "rtk"), []byte("#!/bin/sh\necho 'rtk 0.42.4'\n"), 0755); err != nil {
		test.Fatal(err)
	}
	test.Setenv("PATH", bin)
	claude := filepath.Join(home, ".claude", "settings.json")
	codex := filepath.Join(home, ".codex", "hooks.json")
	os.MkdirAll(filepath.Dir(claude), 0755)
	os.MkdirAll(filepath.Dir(codex), 0755)
	before := []byte("{\"theme\":\"dark\"}\n")
	os.WriteFile(claude, before, 0640)
	os.WriteFile(codex, []byte("{invalid"), 0600)
	var output bytes.Buffer
	if err := Init(bin, "", &output, &output); err == nil {
		test.Fatal("expected malformed Codex settings error")
	}
	after, err := os.ReadFile(claude)
	if err != nil || !bytes.Equal(before, after) {
		test.Fatalf("Claude was not restored: %v %s", err, after)
	}
	info, _ := os.Stat(claude)
	if info.Mode().Perm() != 0640 {
		test.Fatal("permissions changed")
	}
}

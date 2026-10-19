package hook

import (
	"bytes"
	"encoding/json"
	"strings"
	"testing"
)

func TestAgentResponsesAndPreservation(test *testing.T) {
	for _, agent := range []string{"claude", "codex"} {
		payload := map[string]any{"tool_name": "Bash", "tool_input": map[string]any{"command": "git status", "description": "retain", "timeout": 42.0}}
		result := RewritePayload(payload, agent, func() bool { return true }).(map[string]any)["hookSpecificOutput"].(map[string]any)
		decision := "allow"
		if agent == "claude" {
			decision = "ask"
		}
		if result["permissionDecision"] != decision {
			test.Fatal(result)
		}
		updated := result["updatedInput"].(map[string]any)
		if updated["description"] != "retain" || updated["timeout"] != 42.0 || updated["command"] != "rtk git status" {
			test.Fatal(updated)
		}
		if payload["tool_input"].(map[string]any)["command"] != "git status" {
			test.Fatal("mutated original payload")
		}
		if result := RewritePayload(payload, agent, func() bool { return false }); result != nil {
			test.Fatal("rewrote with unsupported RTK")
		}
	}
}

func TestInvalidInputNeverProbes(test *testing.T) {
	for _, raw := range []string{"", "{", "{}{}", "null", "[]", `{"tool_name":"Bash","tool_input":{"command":"curl example.com"}}`} {
		var output bytes.Buffer
		if err := Run("claude", strings.NewReader(raw), &output, func() bool { test.Fatal("unexpected probe"); return false }); err != nil {
			test.Fatal(err)
		}
		if output.Len() != 0 {
			test.Fatal(output.String())
		}
	}
}

func TestHookJSON(test *testing.T) {
	var output bytes.Buffer
	err := Run("codex", strings.NewReader(`{"tool_name":"Bash","tool_input":{"command":"git status"}}`), &output, func() bool { return true })
	if err != nil || !json.Valid(output.Bytes()) {
		test.Fatalf("%v %s", err, output.String())
	}
}

func TestLargeIntegerMetadataIsPreserved(test *testing.T) {
	var output bytes.Buffer
	err := Run("claude", strings.NewReader(`{"tool_name":"Bash","tool_input":{"command":"git status","id":9007199254740993}}`), &output, func() bool { return true })
	if err != nil || !bytes.Contains(output.Bytes(), []byte(`"id":9007199254740993`)) {
		test.Fatalf("metadata changed: %v %s", err, output.String())
	}
}

package hook

import (
	"bytes"
	"encoding/json"
	"io"

	"github.com/bigbizze/rtk-claude-safe/internal/policy"
)

func RewritePayload(payload any, agent string, supported func() bool) any {
	object, ok := payload.(map[string]any)
	if !ok || object["tool_name"] != "Bash" {
		return nil
	}
	input, ok := object["tool_input"].(map[string]any)
	if !ok {
		return nil
	}
	command, ok := input["command"].(string)
	if !ok {
		return nil
	}
	rewritten, ok := policy.Rewrite(command, agent)
	if !ok || !supported() {
		return nil
	}
	updated := make(map[string]any, len(input))
	for key, value := range input {
		updated[key] = value
	}
	updated["command"] = rewritten
	output := map[string]any{"hookEventName": "PreToolUse", "permissionDecision": "allow", "updatedInput": updated}
	if agent == "claude" {
		output["permissionDecision"] = "ask"
		output["permissionDecisionReason"] = "RTK safe rewrite"
	}
	return map[string]any{"hookSpecificOutput": output}
}

func Run(agent string, input io.Reader, output io.Writer, supported func() bool) error {
	raw, err := io.ReadAll(input)
	if err != nil {
		return err
	}
	var payload any
	decoder := json.NewDecoder(bytes.NewReader(raw))
	decoder.UseNumber()
	if !json.Valid(raw) || decoder.Decode(&payload) != nil {
		return nil
	}
	rewritten := RewritePayload(payload, agent, supported)
	if rewritten == nil {
		return nil
	}
	encoder := json.NewEncoder(output)
	encoder.SetEscapeHTML(false)
	return encoder.Encode(rewritten)
}

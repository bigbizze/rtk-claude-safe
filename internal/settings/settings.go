package settings

import (
	"bytes"
	"encoding/json"
	"fmt"
	"io"
	"os"
	"path/filepath"
	"reflect"
	"regexp"
	"runtime"
	"strings"

	"github.com/bigbizze/rtk-claude-safe/internal/install"
)

func Expand(path string) string {
	if path == "~" || strings.HasPrefix(path, "~/") || strings.HasPrefix(path, "~\\") {
		home, err := os.UserHomeDir()
		if err == nil {
			if len(path) == 1 {
				return home
			}
			return filepath.Join(home, path[2:])
		}
	}
	return path
}

func Command(subcommand string) (string, error) {
	executable, err := os.Executable()
	if err != nil {
		return "", err
	}
	executable, err = filepath.Abs(executable)
	if err != nil {
		return "", err
	}
	if runtime.GOOS == "windows" {
		if strings.ContainsAny(executable, " \t\"") {
			return "\"" + strings.ReplaceAll(executable, "\"", "\\\"") + "\" " + subcommand, nil
		}
		return executable + " " + subcommand, nil
	}
	return "'" + strings.ReplaceAll(executable, "'", "'\"'\"'") + "' " + subcommand, nil
}

func Load(path string) (map[string]any, error) {
	raw, err := os.ReadFile(path)
	if os.IsNotExist(err) {
		return map[string]any{}, nil
	}
	if err != nil {
		return nil, err
	}
	if len(bytes.TrimSpace(raw)) == 0 {
		return map[string]any{}, nil
	}
	decoder := json.NewDecoder(bytes.NewReader(raw))
	decoder.UseNumber()
	var result map[string]any
	if err = decoder.Decode(&result); err != nil {
		return nil, fmt.Errorf("%s: %w", path, err)
	}
	if result == nil {
		return nil, fmt.Errorf("%s must contain a JSON object", path)
	}
	var extra any
	if err = decoder.Decode(&extra); err != io.EOF {
		return nil, fmt.Errorf("%s has trailing JSON data", path)
	}
	return result, nil
}

func Write(path string, object map[string]any) error {
	raw, err := json.MarshalIndent(object, "", "  ")
	if err != nil {
		return err
	}
	if err = os.MkdirAll(filepath.Dir(path), 0755); err != nil {
		return err
	}
	mode := os.FileMode(0600)
	if info, statErr := os.Stat(path); statErr == nil {
		mode = info.Mode().Perm()
	}
	return os.WriteFile(path, append(raw, '\n'), mode)
}

func Object(parent map[string]any, key string) (map[string]any, error) {
	value, exists := parent[key]
	if !exists {
		result := map[string]any{}
		parent[key] = result
		return result, nil
	}
	result, ok := value.(map[string]any)
	if !ok {
		return nil, fmt.Errorf("%s is not an object", key)
	}
	return result, nil
}

func List(parent map[string]any, key string) ([]any, error) {
	value, exists := parent[key]
	if !exists {
		return []any{}, nil
	}
	result, ok := value.([]any)
	if !ok {
		return nil, fmt.Errorf("%s is not a list", key)
	}
	return result, nil
}

var pythonName = regexp.MustCompile(`(?i)^python(?:[0-9]+(?:\.[0-9]+)?)?(?:\.exe)?$`)
var envAssignment = regexp.MustCompile(`^[A-Za-z_][A-Za-z0-9_]*=`)

func basename(value string) string {
	return strings.ToLower(filepath.Base(strings.ReplaceAll(strings.Trim(value, "\"'"), "\\", "/")))
}

func commandWords(command string, posix bool) ([]string, bool) {
	var result []string
	var current strings.Builder
	var quote byte
	started := false
	for index := 0; index < len(command); index++ {
		char := command[index]
		if quote != 0 {
			if char == quote {
				quote = 0
				continue
			}
			if posix && quote == '"' && char == '\\' && index+1 < len(command) && (command[index+1] == '"' || command[index+1] == '\\') {
				index++
				char = command[index]
			}
			current.WriteByte(char)
			continue
		}
		if char == '\'' || char == '"' {
			quote = char
			started = true
			continue
		}
		if posix && char == '\\' {
			if index+1 == len(command) {
				return nil, false
			}
			index++
			current.WriteByte(command[index])
			started = true
			continue
		}
		if strings.ContainsRune(" \t\r\n", rune(char)) {
			if started {
				result = append(result, current.String())
				current.Reset()
				started = false
			}
			continue
		}
		current.WriteByte(char)
		started = true
	}
	if quote != 0 {
		return nil, false
	}
	if started {
		result = append(result, current.String())
	}
	return result, true
}

func Managed(command, agent string) bool {
	subcommand := agent + "-hook"
	if agent == "codex-subagent-depth" {
		subcommand = "subagent-depth-hook"
	}
	for _, posix := range []bool{true, false} {
		parts, ok := commandWords(command, posix)
		if !ok || len(parts) == 0 {
			continue
		}
		if len(parts) == 2 && (basename(parts[0]) == "rtk-claude-safe" || basename(parts[0]) == "rtk-claude-safe.exe") && parts[1] == subcommand {
			return true
		}
		if len(parts) == 4 && pythonName.MatchString(basename(parts[0])) && parts[1] == "-m" && parts[2] == "rtk_claude_safe" && parts[3] == subcommand {
			return true
		}
		if agent != "claude" {
			continue
		}
		for len(parts) > 0 && envAssignment.MatchString(parts[0]) {
			parts = parts[1:]
		}
		if len(parts) > 0 && (basename(parts[0]) == "env" || basename(parts[0]) == "env.exe") {
			parts = parts[1:]
			for len(parts) > 0 && envAssignment.MatchString(parts[0]) {
				parts = parts[1:]
			}
		}
		if len(parts) == 3 && (basename(parts[0]) == "rtk" || basename(parts[0]) == "rtk.exe") && parts[1] == "hook" && parts[2] == "claude" {
			return true
		}
	}
	return false
}

func SplitCommand(command string) []string {
	parts, ok := commandWords(command, true)
	if !ok {
		return strings.Fields(command)
	}
	return parts
}

func IsManaged(entry any, agent string) bool {
	hook, ok := entry.(map[string]any)
	if !ok || hook["type"] != "command" {
		return false
	}
	command, ok := hook["command"].(string)
	return ok && Managed(command, agent)
}

func PatchClaude(path, command string) (bool, error) { return patch(path, command, "claude", "Bash") }
func PatchCodex(path, command string) (bool, error) {
	if _, err := os.Stat(filepath.Dir(path)); os.IsNotExist(err) {
		return false, nil
	}
	return patch(path, command, "codex", "^Bash$")
}

func patch(path, command, agent, matcher string) (bool, error) {
	data, err := Load(path)
	if err != nil {
		return false, err
	}
	original, err := Load(path)
	if err != nil {
		return false, err
	}
	root, err := Object(data, "hooks")
	if err != nil {
		return false, err
	}
	groups, err := List(root, "PreToolUse")
	if err != nil {
		return false, err
	}
	if command == "" {
		command, err = Command(agent + "-hook")
		if err != nil {
			return false, err
		}
	}
	entry := map[string]any{"type": "command", "command": command}
	if agent == "codex" {
		entry["timeout"] = json.Number("30")
		entry["statusMessage"] = "RTK safe rewrite"
	}
	var first map[string]any
	for _, value := range groups {
		group, ok := value.(map[string]any)
		if !ok || group["matcher"] != matcher {
			continue
		}
		var hooks []any
		if group["hooks"] != nil {
			hooks, err = List(group, "hooks")
			if err != nil {
				return false, err
			}
		}
		kept := []any{}
		for _, hook := range hooks {
			if !IsManaged(hook, agent) {
				kept = append(kept, hook)
			}
		}
		group["hooks"] = kept
		if first == nil {
			first = group
		}
	}
	if first == nil {
		first = map[string]any{"matcher": matcher, "hooks": []any{}}
		groups = append(groups, first)
	}
	first["hooks"] = append(first["hooks"].([]any), entry)
	root["PreToolUse"] = groups
	if reflect.DeepEqual(original, data) {
		return false, nil
	}
	return true, Write(path, data)
}

func InspectCodex(path string) ([]string, error) {
	raw, err := os.ReadFile(path)
	if os.IsNotExist(err) {
		return nil, nil
	}
	if err != nil {
		return nil, err
	}
	var warnings []string
	inFeatures := false
	for _, line := range strings.Split(string(raw), "\n") {
		line = strings.TrimSpace(strings.SplitN(line, "#", 2)[0])
		if line == "[hooks]" || strings.HasPrefix(line, "[[hooks.") {
			warnings = append(warnings, path+" appears to contain inline hooks; Codex may merge them with hooks.json")
		}
		if strings.HasPrefix(line, "[") {
			inFeatures = line == "[features]"
			continue
		}
		if inFeatures {
			key, value, ok := strings.Cut(line, "=")
			if ok && strings.TrimSpace(key) == "hooks" && strings.EqualFold(strings.TrimSpace(value), "false") {
				warnings = append(warnings, path+" has [features] hooks = false; Codex will not run hooks")
			}
		}
	}
	return warnings, nil
}

type Snapshot struct {
	Path    string
	Data    []byte
	Mode    os.FileMode
	Exists  bool
	Missing []string
}

func Capture(path string) (Snapshot, error) {
	snapshot := Snapshot{Path: path}
	for parent := filepath.Dir(path); ; parent = filepath.Dir(parent) {
		if _, err := os.Stat(parent); err == nil {
			break
		} else if !os.IsNotExist(err) {
			return snapshot, err
		}
		snapshot.Missing = append(snapshot.Missing, parent)
		if parent == filepath.Dir(parent) {
			break
		}
	}
	info, err := os.Stat(path)
	if os.IsNotExist(err) {
		return snapshot, nil
	}
	if err != nil {
		return snapshot, err
	}
	snapshot.Exists = true
	snapshot.Mode = info.Mode().Perm()
	snapshot.Data, err = os.ReadFile(path)
	return snapshot, err
}

func (snapshot Snapshot) Restore() error {
	if snapshot.Exists {
		if err := os.WriteFile(snapshot.Path, snapshot.Data, snapshot.Mode); err != nil {
			return err
		}
		return os.Chmod(snapshot.Path, snapshot.Mode)
	}
	if err := os.Remove(snapshot.Path); err != nil && !os.IsNotExist(err) {
		return err
	}
	for _, parent := range snapshot.Missing {
		os.Remove(parent)
	}
	return nil
}

func Init(installDir, explicitSettings string, stdout, stderr io.Writer) (result error) {
	home, err := os.UserHomeDir()
	if err != nil {
		return err
	}
	claude := filepath.Join(home, ".claude", "settings.json")
	codex := filepath.Join(home, ".codex", "hooks.json")
	_, claudeErr := os.Stat(filepath.Dir(claude))
	_, codexErr := os.Stat(filepath.Dir(codex))
	claudeRequested := explicitSettings != "" || claudeErr == nil
	codexRequested := codexErr == nil && runtime.GOOS != "windows"
	if explicitSettings != "" {
		claude = Expand(explicitSettings)
	}
	if codexErr == nil && runtime.GOOS == "windows" {
		fmt.Fprintln(stderr, "[rtk-claude-safe] warning: skipped Codex; native Windows Codex hooks are not supported")
	}
	if !claudeRequested && !codexRequested {
		fmt.Fprintln(stdout, "[rtk-claude-safe] no supported global agent config folder found (~/.claude or ~/.codex); nothing to do")
		return nil
	}
	if _, err = install.Ensure(Expand(installDir), stderr); err != nil {
		return err
	}
	var snapshots []Snapshot
	for _, item := range []struct {
		requested bool
		path      string
	}{{claudeRequested, claude}, {codexRequested, codex}} {
		if item.requested {
			snapshot, err := Capture(item.path)
			if err != nil {
				return err
			}
			snapshots = append(snapshots, snapshot)
		}
	}
	defer func() {
		if result != nil {
			for index := len(snapshots) - 1; index >= 0; index-- {
				if err := snapshots[index].Restore(); err != nil {
					result = fmt.Errorf("%w; rollback: %v", result, err)
				}
			}
		}
	}()
	var messages []string
	if claudeRequested {
		changed, err := PatchClaude(claude, "")
		if err != nil {
			return err
		}
		messages = append(messages, fmt.Sprintf("[rtk-claude-safe] Claude hook configured in %s (changed: %t)", claude, changed))
	}
	var warnings []string
	if codexRequested {
		changed, err := PatchCodex(codex, "")
		if err != nil {
			return err
		}
		messages = append(messages, fmt.Sprintf("[rtk-claude-safe] Codex hook configured in %s (changed: %t)", codex, changed))
		warnings, err = InspectCodex(filepath.Join(home, ".codex", "config.toml"))
		if err != nil {
			return err
		}
		messages = append(messages, "[rtk-claude-safe] Open Codex CLI, run /hooks, review and trust the rtk-claude-safe hook.")
	}
	for _, message := range messages {
		fmt.Fprintln(stdout, message)
	}
	for _, warning := range warnings {
		fmt.Fprintln(stderr, "[rtk-claude-safe] warning:", warning)
	}
	return nil
}

package depth

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"io"
	"os"
	"os/exec"
	"path/filepath"
	"reflect"
	"regexp"
	"runtime"
	"strconv"
	"time"

	"github.com/bigbizze/rtk-claude-safe/internal/settings"
	"github.com/bigbizze/rtk-claude-safe/internal/store"
)

var codexVersion = regexp.MustCompile(`\b([0-9]+)\.([0-9]+)\.([0-9]+)\b`)

func supportedCodex() error {
	ctx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
	defer cancel()
	command := exec.CommandContext(ctx, "codex", "--version")
	command.WaitDelay = 100 * time.Millisecond
	var stdout, stderr bytes.Buffer
	command.Stdout, command.Stderr = &stdout, &stderr
	if err := command.Run(); err != nil {
		return err
	}
	raw := stdout.String()
	if raw == "" {
		raw = stderr.String()
	}
	version := codexVersion.FindStringSubmatch(raw)
	if version == nil {
		return fmt.Errorf("could not parse Codex version: %s", raw)
	}
	patch, err := strconv.Atoi(version[3])
	if err != nil {
		return err
	}
	if version[1] != "0" || version[2] != "144" || patch < 6 {
		return fmt.Errorf("Codex subagent depth enforcement supports >=0.144.6,<0.145.0; found %s", raw)
	}
	return nil
}

func patchHooks(path, command string, remove bool) (bool, error) {
	if remove {
		if _, err := os.Stat(path); os.IsNotExist(err) {
			return false, nil
		}
	}
	data, err := settings.Load(path)
	if err != nil {
		return false, err
	}
	original, err := settings.Load(path)
	if err != nil {
		return false, err
	}
	root, err := settings.Object(data, "hooks")
	if err != nil {
		return false, err
	}
	for _, event := range []string{"SessionStart", "PreToolUse", "PostToolUse"} {
		groups, err := settings.List(root, event)
		if err != nil {
			return false, err
		}
		kept := []any{}
		for _, value := range groups {
			group, ok := value.(map[string]any)
			if !ok {
				kept = append(kept, value)
				continue
			}
			if group["hooks"] == nil {
				group["hooks"] = []any{}
				kept = append(kept, group)
				continue
			}
			hooks, err := settings.List(group, "hooks")
			if err != nil {
				return false, err
			}
			remaining := []any{}
			for _, hook := range hooks {
				if !settings.IsManaged(hook, "codex-subagent-depth") {
					remaining = append(remaining, hook)
				}
			}
			group["hooks"] = remaining
			extra := false
			for key := range group {
				if key != "matcher" && key != "hooks" {
					extra = true
				}
			}
			if len(remaining) > 0 || extra {
				kept = append(kept, group)
			}
		}
		if len(kept) == 0 {
			delete(root, event)
		} else {
			root[event] = kept
		}
		if !remove {
			message := "Subagent depth policy"
			if event == "PreToolUse" {
				message = "Subagent depth gate"
			}
			if event == "PostToolUse" {
				message = "Subagent depth record"
			}
			group := map[string]any{"hooks": []any{map[string]any{"type": "command", "command": command, "timeout": json.Number("10"), "statusMessage": message}}}
			if event != "SessionStart" {
				group["matcher"] = "^Agent$"
			}
			root[event] = append(kept, group)
		}
	}
	if remove && len(root) == 0 {
		delete(data, "hooks")
	}
	if reflect.DeepEqual(data, original) {
		return false, nil
	}
	return true, settings.Write(path, data)
}

func Enforce(stdout, stderr io.Writer) (result error) {
	if runtime.GOOS == "windows" {
		return fmt.Errorf("native Windows Codex hooks are not supported")
	}
	if err := supportedCodex(); err != nil {
		return err
	}
	home := homeDir()
	hooks := filepath.Join(home, "hooks.json")
	snapshot, err := settings.Capture(hooks)
	if err != nil {
		return err
	}
	defer func() {
		if result != nil {
			if err := snapshot.Restore(); err != nil {
				result = fmt.Errorf("%w; rollback: %v", result, err)
			}
		}
	}()
	if err = os.MkdirAll(stateDir(home), 0700); err != nil {
		return err
	}
	unlock, err := store.Lock(lockPath(home), true)
	if err != nil {
		return err
	}
	defer unlock()
	database, err := connect(home)
	if err != nil {
		return err
	}
	database.Close()
	command, err := settings.Command("subagent-depth-hook")
	if err != nil {
		return err
	}
	changed, err := patchHooks(hooks, command, false)
	if err != nil {
		return err
	}
	if err = os.Remove(markerPath(home)); err != nil && !os.IsNotExist(err) {
		return err
	}
	fmt.Fprintf(stdout, "[rtk-claude-safe] Codex subagent depth hooks configured in %s (changed: %t)\n", hooks, changed)
	fmt.Fprintln(stdout, "[rtk-claude-safe] Open Codex CLI, run /hooks, review and trust the subagent depth hooks.")
	warnings, err := settings.InspectCodex(filepath.Join(home, "config.toml"))
	if err != nil {
		fmt.Fprintln(stderr, "[rtk-claude-safe] warning:", err)
	} else {
		for _, warning := range warnings {
			fmt.Fprintln(stderr, "[rtk-claude-safe] warning:", warning)
		}
	}
	return nil
}

func Remove(stdout, stderr io.Writer) error {
	home := homeDir()
	if err := os.MkdirAll(stateDir(home), 0700); err != nil {
		return err
	}
	if err := os.WriteFile(markerPath(home), []byte("disabled\n"), 0600); err != nil {
		return err
	}
	changed, err := patchHooks(filepath.Join(home, "hooks.json"), "", true)
	if err != nil {
		return err
	}
	unlock, err := store.Lock(lockPath(home), true)
	if err != nil {
		return err
	}
	defer unlock()
	for _, suffix := range []string{"", "-wal", "-shm"} {
		if err = os.Remove(databasePath(home) + suffix); err != nil && !os.IsNotExist(err) {
			return err
		}
	}
	fmt.Fprintf(stdout, "[rtk-claude-safe] removed Codex subagent depth hooks (changed: %t)\n", changed)
	fmt.Fprintln(stdout, "[rtk-claude-safe] disabled marker retained for already-loaded hooks:", markerPath(home))
	return nil
}

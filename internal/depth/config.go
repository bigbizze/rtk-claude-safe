package depth

import (
	"fmt"
	"os"
	"path/filepath"
	"sort"
	"strings"

	"github.com/bigbizze/rtk-claude-safe/internal/settings"
	"github.com/pelletier/go-toml/v2"
)

func homeDir() string {
	home := os.Getenv("CODEX_HOME")
	if home != "" {
		return settings.Expand(home)
	}
	home, _ = os.UserHomeDir()
	return filepath.Join(home, ".codex")
}
func stateDir(home string) string     { return filepath.Join(home, "rtk-claude-safe") }
func markerPath(home string) string   { return filepath.Join(stateDir(home), "subagent-depth.disabled") }
func databasePath(home string) string { return filepath.Join(stateDir(home), "subagent-depth.sqlite") }
func lockPath(home string) string     { return filepath.Join(stateDir(home), "subagent-depth.lock") }

func readConfig(path string) (map[string]any, error) {
	raw, err := os.ReadFile(path)
	if os.IsNotExist(err) {
		return nil, nil
	}
	if err != nil {
		return nil, err
	}
	var config map[string]any
	if err = toml.Unmarshal(raw, &config); err != nil {
		return nil, fmt.Errorf("%s: %w", path, err)
	}
	return config, nil
}

func configDepth(config map[string]any, path string) (*int64, error) {
	value, exists := config["agents"]
	if !exists {
		return nil, nil
	}
	agents, ok := value.(map[string]any)
	if !ok {
		return nil, fmt.Errorf("%s: [agents] must be a table", path)
	}
	value, exists = agents["max_depth"]
	if !exists {
		return nil, nil
	}
	depth, ok := value.(int64)
	if !ok {
		return nil, fmt.Errorf("%s: agents.max_depth must be an integer", path)
	}
	if depth < 1 || depth > 2147483647 {
		return nil, fmt.Errorf("%s: agents.max_depth must be between 1 and 2147483647", path)
	}
	return &depth, nil
}

func directory(path string) string {
	path = settings.Expand(path)
	if info, err := os.Stat(path); err == nil && !info.IsDir() {
		path = filepath.Dir(path)
	}
	if !filepath.IsAbs(path) {
		path, _ = filepath.Abs(path)
		if resolved, err := filepath.EvalSymlinks(path); err == nil {
			path = resolved
		}
	}
	return filepath.Clean(path)
}

func ancestors(path string) []string {
	var result []string
	for {
		result = append(result, path)
		parent := filepath.Dir(path)
		if parent == path {
			break
		}
		path = parent
	}
	return result
}

func canonical(path string) string {
	resolved, err := filepath.EvalSymlinks(path)
	if err == nil {
		return resolved
	}
	absolute, _ := filepath.Abs(path)
	return absolute
}

func gitRoots(cwd string) (string, string) {
	for _, ancestor := range ancestors(cwd) {
		marker := filepath.Join(ancestor, ".git")
		info, err := os.Stat(marker)
		if err != nil {
			continue
		}
		if info.IsDir() {
			return ancestor, ancestor
		}
		raw, err := os.ReadFile(marker)
		if err != nil {
			return ancestor, ancestor
		}
		first := strings.SplitN(string(raw), "\n", 2)[0]
		if !strings.HasPrefix(first, "gitdir:") {
			return ancestor, ancestor
		}
		gitdir := strings.TrimSpace(strings.TrimPrefix(first, "gitdir:"))
		if !filepath.IsAbs(gitdir) {
			gitdir = canonical(filepath.Join(ancestor, gitdir))
		}
		raw, err = os.ReadFile(filepath.Join(gitdir, "commondir"))
		if err != nil {
			return ancestor, ancestor
		}
		common := strings.TrimSpace(string(raw))
		if !filepath.IsAbs(common) {
			common = canonical(filepath.Join(gitdir, common))
		}
		if filepath.Base(common) == ".git" {
			common = filepath.Dir(common)
		}
		return ancestor, common
	}
	return "", ""
}

func trustLevel(dir, cwd string, trust map[string]string) string {
	checkout, repo := gitRoots(cwd)
	project := checkout
	if project == "" {
		project = cwd
	}
	for _, candidate := range []string{dir, project, checkout, repo} {
		if candidate == "" {
			continue
		}
		for _, key := range []string{canonical(candidate), candidate} {
			if level, exists := trust[key]; exists {
				return level
			}
		}
	}
	return ""
}

func resolve(cwd, home, systemPath string) (*int64, error) {
	cwd = directory(cwd)
	var effective *int64
	trust := map[string]string{}
	for _, path := range []string{systemPath, filepath.Join(home, "config.toml")} {
		config, err := readConfig(path)
		if err != nil {
			return nil, err
		}
		limit, err := configDepth(config, path)
		if err != nil {
			return nil, err
		}
		if limit != nil {
			effective = limit
		}
		projects, _ := config["projects"].(map[string]any)
		for key, value := range projects {
			project, _ := value.(map[string]any)
			level, _ := project["trust_level"].(string)
			if level == "trusted" || level == "untrusted" {
				trust[key] = level
			}
		}
	}
	parents := ancestors(cwd)
	for index := len(parents) - 1; index >= 0; index-- {
		dir := parents[index]
		dotCodex := filepath.Join(dir, ".codex")
		if canonical(dotCodex) == canonical(home) || trustLevel(dir, cwd, trust) != "trusted" {
			continue
		}
		path := filepath.Join(dotCodex, "config.toml")
		config, err := readConfig(path)
		if err != nil {
			return nil, err
		}
		limit, err := configDepth(config, path)
		if err != nil {
			return nil, err
		}
		if limit != nil {
			effective = limit
		}
	}
	return effective, nil
}

func sortedKeys(object map[string]any) []string {
	keys := make([]string, 0, len(object))
	for key := range object {
		keys = append(keys, key)
	}
	sort.Strings(keys)
	return keys
}

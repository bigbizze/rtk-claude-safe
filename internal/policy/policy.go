package policy

import (
	"path"
	"regexp"
	"slices"
	"strings"
)

var assignment = regexp.MustCompile(`^[A-Za-z_][A-Za-z0-9_]*=`)
var packagePart = regexp.MustCompile(`^[A-Za-z0-9][A-Za-z0-9_.-]*$`)
var machineFlags = strings.Fields("--json --jq --template --format --name-only --raw --numstat --name-status --output --outputFile --output-file --junit-xml --junitxml -json --parseable -z --null -q -t")
var inlineFlags = strings.Fields("--json --jq --template --format --porcelain --output --outputFile --output-file --output-format")
var formatFlags = strings.Fields("-f --reporter --output-format")
var formats = strings.Fields("json json-lines github gitlab junit junit-xml xml sarif checkstyle tap")
var reportPrefixes = strings.Fields("--junit-xml --junitxml --cov-report --json-report --json-report-file --xml-report --html-report --linecount-report --any-exprs-report --cobertura-xml-report --txt-report")
var serverWords = strings.Fields("dev start serve server preview storybook watch")

const unsafePath = "$`*?[]{};&|<>!"

func Rewrite(command, agent string) (string, bool) {
	if strings.ContainsAny(command, "\n`$") || strings.Contains(command, "<(") || strings.Contains(command, ">(") {
		return "", false
	}
	segments, separators, ok := splitList(command)
	if !ok {
		return "", false
	}
	parts := make([][]string, len(segments))
	for index, segment := range segments {
		parts[index], ok = splitWords(segment)
		if !ok {
			return "", false
		}
	}
	if len(separators) == 0 {
		return rewriteSegment(parts[0])
	}
	rewritten := make([]string, len(parts))
	changed := false
	for index, words := range parts {
		if replacement, allowed := rewriteSegment(words); allowed {
			rewritten[index] = replacement
			changed = true
		} else if preserve(words) {
			rewritten[index] = shellJoin(words)
		}
	}
	if !changed {
		return "", false
	}
	for index, replacement := range rewritten {
		if replacement != "" {
			continue
		}
		if agent != "codex" || index+1 >= len(parts) || separators[index] != "&&" {
			return "", false
		}
		next, allowed := rewriteSegment(parts[index+1])
		if !allowed {
			return "", false
		}
		words := parts[index]
		gofmt := safeGofmt(words) && strings.HasPrefix(next, "rtk go test")
		cargo := (slices.Equal(words, []string{"cargo", "fmt"}) || slices.Equal(words, []string{"cargo", "fmt", "--all"})) && executable(parts[index+1][0]) == "cargo" && len(parts[index+1]) > 1 && slices.Contains([]string{"test", "check", "clippy"}, parts[index+1][1])
		if !gofmt && !cargo {
			return "", false
		}
		rewritten[index] = shellJoin(words)
	}
	result := rewritten[0]
	for index, separator := range separators {
		result += " " + separator + " " + rewritten[index+1]
	}
	return result, true
}

func executable(value string) string { return lower(path.Base(value)) }

func preserve(parts []string) bool {
	if len(parts) == 0 || assignment.MatchString(parts[0]) {
		return false
	}
	if slices.Contains([]string{"true", "false", ":"}, parts[0]) {
		return len(parts) == 1
	}
	if parts[0] != "cd" {
		return false
	}
	args := parts[1:]
	if len(args) == 0 || slices.Equal(args, []string{"-"}) {
		return true
	}
	if args[0] == "--" {
		args = args[1:]
		if len(args) == 0 {
			return true
		}
	}
	return len(args) == 1 && !strings.HasPrefix(args[0], "-") && !strings.HasPrefix(args[0], "~") && !strings.ContainsAny(args[0], unsafePath)
}

func safeGofmt(parts []string) bool {
	if len(parts) < 3 || parts[0] != "gofmt" || parts[1] != "-w" {
		return false
	}
	for _, arg := range parts[2:] {
		if !strings.HasSuffix(arg, ".go") || strings.HasPrefix(arg, "-") || strings.HasPrefix(arg, "~") || strings.HasPrefix(arg, "/") || slices.Contains(strings.Split(arg, "/"), "..") || strings.ContainsAny(arg, unsafePath) {
			return false
		}
	}
	return true
}

func machineFormat(value string) bool {
	value = lower(value)
	return slices.Contains(formats, value) || strings.HasPrefix(value, "json")
}

func denied(parts []string) bool {
	for index := 1; index < len(parts); index++ {
		part := parts[index]
		if executable(parts[0]) == "cargo" && part == "-q" {
			continue
		}
		if slices.Contains(machineFlags, part) || part == "-w" || strings.HasPrefix(part, "--watch") || strings.HasPrefix(part, "--porcelain") || strings.HasPrefix(part, "--coverage") {
			return true
		}
		flag, value, inline := strings.Cut(part, "=")
		if inline && (slices.Contains(inlineFlags, flag) || flag == "-json" || (slices.Contains(formatFlags, flag) && machineFormat(value))) {
			return true
		}
		for _, prefix := range reportPrefixes {
			if strings.HasPrefix(part, prefix) {
				return true
			}
		}
		if slices.Contains(formatFlags, part) && index+1 < len(parts) && machineFormat(parts[index+1]) {
			return true
		}
	}
	return false
}

func rewriteSegment(parts []string) (string, bool) {
	if len(parts) == 0 || assignment.MatchString(parts[0]) {
		return "", false
	}
	command := executable(parts[0])
	if command == "rtk" || command == "rtk.exe" || (command == "env" && len(parts) > 1) || denied(parts) {
		return "", false
	}
	allowed := false
	args := parts[1:]
	switch command {
	case "cargo":
		allowed = cargoArgs(args)
	case "git":
		allowed = gitArgs(args)
	case "npm", "pnpm":
		allowed = packageManager(parts)
	case "npx":
		allowed = npxArgs(args)
	case "gh":
		allowed = ghArgs(args)
	case "prisma":
		allowed = prismaArgs(args)
	case "pip":
		allowed = len(args) > 0 && slices.Contains([]string{"list", "outdated", "show"}, args[0])
	case "eslint":
		return shellJoin(append([]string{"rtk", "lint"}, args...)), true
	case "vitest", "jest", "pytest", "tsc", "mypy":
		allowed = true
	case "go":
		allowed = len(args) > 0 && args[0] == "test"
		for _, arg := range args {
			if strings.HasPrefix(arg, "-bench") {
				allowed = false
			}
		}
	case "ruff":
		allowed = len(args) > 0 && (args[0] == "check" || (len(args) > 1 && args[0] == "format" && args[1] == "--check"))
	case "prettier":
		allowed = len(args) > 0 && args[0] == "--check"
	case "next":
		allowed = len(args) > 0 && args[0] == "build"
	case "tree", "wc":
		allowed = true
	case "env":
		allowed = len(args) == 0
	}
	if !allowed {
		return "", false
	}
	return shellJoin(append([]string{"rtk"}, parts...)), true
}

func cargoArgs(args []string) bool {
	if len(args) == 0 {
		return false
	}
	for index := 1; index < len(args); index++ {
		if args[index] == "--message-format" {
			if index+1 < len(args) && strings.HasPrefix(args[index+1], "json") {
				return false
			}
			break
		}
		if strings.HasPrefix(args[index], "--message-format=json") {
			return false
		}
	}
	return slices.Contains([]string{"test", "build", "clippy", "check", "doc", "nextest"}, args[0]) || slices.Equal(args, []string{"fmt", "--check"}) || slices.Equal(args, []string{"fmt", "--all", "--check"})
}

func exactAny(args []string, choices ...[]string) bool {
	for _, choice := range choices {
		if slices.Equal(args, choice) {
			return true
		}
	}
	return false
}

func gitArgs(args []string) bool {
	if len(args) == 0 {
		return false
	}
	command, rest := args[0], args[1:]
	switch command {
	case "status":
		return exactAny(rest, nil, []string{"--short"}, []string{"-s"}, []string{"--short", "--branch"}, []string{"--branch", "--short"}, []string{"-sb"})
	case "rev-parse":
		return slices.Equal(rest, []string{"HEAD"})
	case "branch":
		return exactAny(rest, []string{"--show-current"}, []string{"-vv"}, []string{"--list"})
	case "stash", "worktree":
		return slices.Equal(rest, []string{"list"})
	case "diff":
		return exactAny(rest, []string{"--stat"}, []string{"--cached", "--stat"}, []string{"--stat", "--cached"}, []string{"--check"})
	case "log":
		if !slices.Contains(rest, "--oneline") {
			return false
		}
		count := false
		for index := 0; index < len(rest); index++ {
			arg := rest[index]
			if arg == "--oneline" {
				continue
			}
			value := ""
			if arg == "-n" || arg == "--max-count" {
				index++
				if index >= len(rest) {
					return false
				}
				value = rest[index]
			} else if strings.HasPrefix(arg, "--max-count=") {
				value = strings.TrimPrefix(arg, "--max-count=")
			} else {
				return false
			}
			if !smallInteger(value) {
				return false
			}
			count = true
		}
		return count
	}
	return false
}

func safeScript(script string, direct bool) bool {
	lower := lower(script)
	for _, word := range serverWords {
		if strings.Contains(lower, word) {
			return false
		}
	}
	if direct {
		return slices.Contains([]string{"test", "lint", "build", "typecheck", "boundaries"}, script) || strings.HasPrefix(script, "test:") || strings.HasPrefix(script, "check:")
	}
	for _, root := range []string{"test", "lint", "build", "typecheck", "check", "format:check"} {
		if lower == root || strings.HasPrefix(lower, root+":") {
			return true
		}
	}
	return false
}

func packageSelector(selector string) bool {
	if strings.ContainsAny(selector, "*?[]{}!^:,") || strings.Contains(selector, "...") {
		return false
	}
	if strings.HasPrefix(selector, "@") {
		parts := strings.Split(selector[1:], "/")
		return len(parts) == 2 && packagePart.MatchString(parts[0]) && packagePart.MatchString(parts[1])
	}
	return packagePart.MatchString(selector)
}

func packageManager(parts []string) bool {
	args := parts[1:]
	if len(args) == 0 {
		return false
	}
	if executable(parts[0]) == "npm" {
		return args[0] == "install" || (len(args) > 1 && args[0] == "run" && safeScript(args[1], false))
	}
	if slices.Contains([]string{"install", "list", "outdated"}, args[0]) {
		return true
	}
	if parts[0] == "pnpm" && len(args) == 1 && safeScript(args[0], true) {
		return true
	}
	if len(args) > 1 && args[0] == "run" && safeScript(args[1], false) {
		return true
	}
	if len(args) > 1 && args[0] == "exec" {
		tool := executable(args[1])
		rest := args[2:]
		return tool == "tsc" || tool == "eslint" || (tool == "prettier" && len(rest) > 0 && rest[0] == "--check") || (tool == "vitest" && len(rest) > 0 && rest[0] == "run") || (tool == "prisma" && slices.Equal(rest, []string{"generate"}))
	}
	return parts[0] == "pnpm" && len(args) == 3 && args[0] == "--filter" && packageSelector(args[1]) && slices.Contains([]string{"typecheck", "test", "build"}, args[2])
}

func npxArgs(args []string) bool {
	if len(args) == 0 {
		return false
	}
	tool, rest := executable(args[0]), args[1:]
	return tool == "tsc" || tool == "eslint" || (tool == "vitest" && len(rest) > 0 && rest[0] == "run") || (tool == "prettier" && len(rest) > 0 && rest[0] == "--check") || (tool == "playwright" && len(rest) > 0 && rest[0] == "codegen") || (tool == "prisma" && prismaArgs(rest))
}

func prismaArgs(args []string) bool {
	return len(args) > 0 && (args[0] == "generate" || (len(args) > 1 && ((args[0] == "db" && args[1] == "push") || (args[0] == "migrate" && args[1] == "dev"))))
}

func ghArgs(args []string) bool {
	if len(args) < 2 {
		return false
	}
	area, action := args[0], args[1]
	comments := (area == "pr" || area == "issue") && action == "view"
	for _, arg := range args[2:] {
		if strings.HasPrefix(arg, "-") && !strings.HasPrefix(arg, "--") {
			flags, _, _ := strings.Cut(arg[1:], "=")
			if strings.ContainsAny(flags, "wqt") || (comments && strings.Contains(flags, "c")) {
				return false
			}
		}
		flag, _, _ := strings.Cut(arg, "=")
		if flag == "--web" || (comments && flag == "--comments") {
			return false
		}
	}
	return slices.Contains([]string{"pr", "issue", "run", "workflow", "repo"}, area) && (action == "list" || action == "view")
}

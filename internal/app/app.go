package app

import (
	"errors"
	"flag"
	"fmt"
	"io"
	"os"
	"path/filepath"

	"github.com/bigbizze/rtk-claude-safe/internal/depth"
	"github.com/bigbizze/rtk-claude-safe/internal/hook"
	"github.com/bigbizze/rtk-claude-safe/internal/rtkruntime"
	"github.com/bigbizze/rtk-claude-safe/internal/settings"
	"github.com/bigbizze/rtk-claude-safe/internal/sqlitemaint"
)

const Version = "0.7.0"

const usage = `Usage: rtk-claude-safe <command> [options]

Commands:
  init                                Install RTK and configure existing agents
  enforce-subagent-depth              Configure Codex subagent depth enforcement
  remove-subagent-depth-enforcement    Remove Codex subagent depth enforcement
  repair-codex-sqlite                  Suppress low-level Codex log inserts
  revert-codex-sqlite-repair            Remove the Codex log filter
  vacuum-codex-sqlite                  Compact the Codex log database

Options:
  --help, -h     Show help
  --version      Show version
`

func Run(args []string, input io.Reader, output, diagnostic io.Writer) int {
	if len(args) == 0 {
		fmt.Fprint(diagnostic, usage)
		return 2
	}
	var err error
	switch args[0] {
	case "--version":
		fmt.Fprintln(output, "rtk-claude-safe", Version)
		return 0
	case "--help", "-h", "help":
		fmt.Fprint(output, usage)
		return 0
	case "claude-hook":
		err = hook.Run("claude", input, output, rtkruntime.Supported)
	case "codex-hook":
		err = hook.Run("codex", input, output, rtkruntime.Supported)
	case "subagent-depth-hook":
		err = depth.Run(input, output)
	default:
		return runManagement(args, input, output, diagnostic)
	}
	if err != nil {
		fmt.Fprintln(diagnostic, "[rtk-claude-safe]", err)
		return 1
	}
	return 0
}

func runManagement(args []string, input io.Reader, output, diagnostic io.Writer) int {
	command := args[0]
	flags := flag.NewFlagSet(command, flag.ContinueOnError)
	flags.SetOutput(diagnostic)
	var installDir, settingsPath, database string
	var backup bool
	switch command {
	case "init":
		home, err := os.UserHomeDir()
		if err != nil {
			fmt.Fprintln(diagnostic, err)
			return 1
		}
		flags.StringVar(&installDir, "install-dir", filepath.Join(home, ".local", "bin"), "RTK installation directory")
		flags.StringVar(&settingsPath, "settings", "", "Explicit Claude settings.json path")
	case "repair-codex-sqlite", "revert-codex-sqlite-repair", "vacuum-codex-sqlite":
		flags.StringVar(&database, "database", "", "Explicit Codex logs database path")
		if command == "vacuum-codex-sqlite" {
			flags.BoolVar(&backup, "backup", false, "Back up the database before maintenance")
		}
	case "enforce-subagent-depth", "remove-subagent-depth-enforcement":
	default:
		fmt.Fprintf(diagnostic, "Unknown command: %s\n%s", command, usage)
		return 2
	}
	if err := flags.Parse(args[1:]); err != nil {
		if errors.Is(err, flag.ErrHelp) {
			return 0
		}
		return 2
	}
	if flags.NArg() != 0 {
		fmt.Fprintf(diagnostic, "Unexpected arguments: %v\n", flags.Args())
		return 2
	}
	var err error
	switch command {
	case "init":
		err = settings.Init(installDir, settingsPath, output, diagnostic)
	case "enforce-subagent-depth":
		err = depth.Enforce(output, diagnostic)
	case "remove-subagent-depth-enforcement":
		err = depth.Remove(output, diagnostic)
	case "repair-codex-sqlite":
		err = sqlitemaint.Repair(database, input, output)
	case "revert-codex-sqlite-repair":
		err = sqlitemaint.Revert(database, input, output)
	case "vacuum-codex-sqlite":
		err = sqlitemaint.Vacuum(database, backup, input, output)
	}
	if err != nil {
		fmt.Fprintln(diagnostic, "[rtk-claude-safe]", err)
		return 1
	}
	return 0
}

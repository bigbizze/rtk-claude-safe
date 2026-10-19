package sqlitemaint

import (
	"bufio"
	"context"
	"database/sql"
	"fmt"
	"io"
	"os"
	"os/exec"
	"path/filepath"
	"runtime"
	"strconv"
	"strings"
	"time"

	"github.com/bigbizze/rtk-claude-safe/internal/settings"
	"github.com/bigbizze/rtk-claude-safe/internal/store"
)

const triggerName = "codex_ignore_low_level_logs"
const triggerSQL = `CREATE TRIGGER codex_ignore_low_level_logs
BEFORE INSERT ON logs
WHEN upper(NEW.level) IN ('TRACE', 'DEBUG', 'INFO')
BEGIN
  SELECT RAISE(IGNORE);
END`

func resolve(path string) string {
	if path != "" {
		return settings.Expand(path)
	}
	if path = os.Getenv("CODEX_SQLITE_HOME"); path != "" {
		path = settings.Expand(path)
		info, err := os.Stat(path)
		if err == nil {
			if info.IsDir() {
				return filepath.Join(path, "logs_2.sqlite")
			}
			return path
		}
		ext := strings.ToLower(filepath.Ext(path))
		if ext == ".sqlite" || ext == ".sqlite3" || ext == ".db" {
			return path
		}
		return filepath.Join(path, "logs_2.sqlite")
	}
	home := os.Getenv("CODEX_HOME")
	if home == "" {
		userHome, _ := os.UserHomeDir()
		home = filepath.Join(userHome, ".codex")
	}
	return filepath.Join(settings.Expand(home), "logs_2.sqlite")
}

func activeProcesses() ([]string, error) {
	if runtime.GOOS == "windows" {
		return nil, fmt.Errorf("native Windows Codex SQLite maintenance is not supported")
	}
	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()
	command := exec.CommandContext(ctx, "ps", "-ax", "-o", "pid=", "-o", "command=")
	command.WaitDelay = 100 * time.Millisecond
	raw, err := command.Output()
	if err != nil {
		return nil, fmt.Errorf("could not inspect process list: %w", err)
	}
	var active []string
	for _, line := range strings.Split(string(raw), "\n") {
		fields := strings.Fields(line)
		if len(fields) < 2 {
			continue
		}
		pid, _ := strconv.Atoi(fields[0])
		if pid == os.Getpid() {
			continue
		}
		commandLine := strings.TrimSpace(strings.TrimSpace(line)[len(fields[0]):])
		if isCodexProcess(commandLine) {
			active = append(active, strings.TrimSpace(line))
		}
	}
	return active, nil
}

func isCodexProcess(command string) bool {
	if strings.Contains(command, "codex-code-mode-host") {
		return false
	}
	parts := settings.SplitCommand(command)
	if len(parts) == 0 {
		return false
	}
	name := strings.ToLower(filepath.Base(strings.ReplaceAll(parts[0], "\\", "/")))
	if name == "codex" || name == "codex.exe" {
		return true
	}
	if name != "node" && name != "node.exe" {
		return false
	}
	for _, arg := range parts[1:] {
		if strings.HasPrefix(arg, "-") {
			continue
		}
		name = strings.ToLower(filepath.Base(strings.ReplaceAll(arg, "\\", "/")))
		return name == "codex" || name == "codex.exe"
	}
	return false
}

func confirm(input io.Reader, output io.Writer) error {
	fmt.Fprintln(output, "[rtk-claude-safe] Close all Codex CLI sessions before continuing.")
	scanner := bufio.NewScanner(input)
	for {
		fmt.Fprint(output, "[rtk-claude-safe] Enter exactly \"Y\" when they are closed: ")
		if !scanner.Scan() {
			return fmt.Errorf("confirmation aborted before receiving Y")
		}
		if scanner.Text() != "Y" {
			return fmt.Errorf("aborted; enter exactly Y to continue")
		}
		active, err := activeProcesses()
		if err != nil {
			return err
		}
		if len(active) == 0 {
			break
		}
		fmt.Fprintln(output, "[rtk-claude-safe] Codex still appears to be running:")
		for index, line := range active {
			if index == 10 {
				break
			}
			fmt.Fprintln(output, line)
		}
	}
	active, err := activeProcesses()
	if err != nil {
		return err
	}
	if len(active) > 0 {
		return fmt.Errorf("Codex is still running; close all sessions before mutating the log database")
	}
	return nil
}

func open(path string) (*sql.DB, error) {
	info, err := os.Stat(path)
	if err != nil {
		return nil, err
	}
	if !info.Mode().IsRegular() {
		return nil, fmt.Errorf("database is not a regular file: %s", path)
	}
	return store.Open(path, "rw", 2000)
}

func normalize(statement string) string {
	return strings.Join(strings.Fields(strings.TrimRight(strings.TrimSpace(statement), ";")), " ")
}

func changeTrigger(path string, remove bool) (bool, error) {
	database, err := open(path)
	if err != nil {
		return false, err
	}
	defer database.Close()
	if !remove {
		var count int
		if err = database.QueryRow("SELECT count(*) FROM pragma_table_info('logs') WHERE name='level'").Scan(&count); err != nil {
			return false, err
		}
		if count != 1 {
			return false, fmt.Errorf("Codex SQLite logs table has no level column")
		}
	}
	var existing string
	err = database.QueryRow("SELECT sql FROM sqlite_master WHERE type='trigger' AND name=?", triggerName).Scan(&existing)
	if err != nil && err != sql.ErrNoRows {
		return false, err
	}
	if err == nil && normalize(existing) != normalize(triggerSQL) {
		return false, fmt.Errorf("trigger %s exists with different SQL", triggerName)
	}
	if remove {
		if err == sql.ErrNoRows {
			return false, nil
		}
		_, err = database.Exec("DROP TRIGGER " + triggerName)
	} else {
		if err == nil {
			return false, nil
		}
		_, err = database.Exec(triggerSQL)
	}
	return err == nil, err
}

func Repair(path string, input io.Reader, output io.Writer) error {
	return runTrigger(path, false, input, output)
}
func Revert(path string, input io.Reader, output io.Writer) error {
	return runTrigger(path, true, input, output)
}
func runTrigger(path string, remove bool, input io.Reader, output io.Writer) error {
	if err := confirm(input, output); err != nil {
		return err
	}
	path = resolve(path)
	changed, err := changeTrigger(path, remove)
	if err != nil {
		return err
	}
	fmt.Fprintf(output, "[rtk-claude-safe] Codex SQLite trigger in %s (removed: %t, changed: %t)\n", path, remove, changed)
	return nil
}

func size(path string) int64 {
	info, err := os.Stat(path)
	if err != nil {
		return 0
	}
	return info.Size()
}

func vacuum(path string, backup bool) (string, error) {
	before := size(path) + size(path+"-wal")
	factor := int64(2)
	if backup {
		factor = 3
	}
	free, err := store.FreeSpace(filepath.Dir(path))
	if err != nil {
		return "", err
	}
	if uint64(before*factor) > free {
		return "", fmt.Errorf("not enough free disk space for VACUUM")
	}
	database, err := open(path)
	if err != nil {
		return "", err
	}
	defer database.Close()
	backupPath := ""
	if backup {
		base := path + ".backup-" + time.Now().Format("20060102-150405")
		backupPath = base
		for suffix := 2; ; suffix++ {
			if _, err = os.Stat(backupPath); os.IsNotExist(err) {
				break
			} else if err != nil {
				return "", err
			}
			backupPath = base + "-" + strconv.Itoa(suffix)
		}
		if _, err = database.Exec("VACUUM INTO ?", backupPath); err != nil {
			return "", err
		}
	}
	var busy, log, checkpointed int
	if err = database.QueryRow("PRAGMA wal_checkpoint(TRUNCATE)").Scan(&busy, &log, &checkpointed); err != nil {
		return backupPath, err
	}
	if busy != 0 {
		return backupPath, fmt.Errorf("WAL checkpoint was busy; another process still has the database open")
	}
	if _, err = database.Exec("VACUUM"); err != nil {
		return backupPath, err
	}
	_, err = database.Exec("PRAGMA optimize")
	return backupPath, err
}

func Vacuum(path string, backup bool, input io.Reader, output io.Writer) error {
	if err := confirm(input, output); err != nil {
		return err
	}
	path = resolve(path)
	before := size(path)
	backupPath, err := vacuum(path, backup)
	if err != nil {
		return err
	}
	fmt.Fprintf(output, "[rtk-claude-safe] vacuumed %s: %d -> %d bytes\n", path, before, size(path))
	if backupPath != "" {
		fmt.Fprintln(output, "[rtk-claude-safe] backup written:", backupPath)
	}
	return nil
}

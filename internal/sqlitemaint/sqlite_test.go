package sqlitemaint

import (
	"bytes"
	"os"
	"path/filepath"
	"runtime"
	"strings"
	"testing"

	"github.com/bigbizze/rtk-claude-safe/internal/store"
)

func createLogs(test *testing.T) string {
	test.Helper()
	path := filepath.Join(test.TempDir(), "logs.sqlite")
	database, err := store.Open(path, "rwc", 2000)
	if err != nil {
		test.Fatal(err)
	}
	defer database.Close()
	if _, err = database.Exec("CREATE TABLE logs(level TEXT,message TEXT)"); err != nil {
		test.Fatal(err)
	}
	return path
}

func TestTriggerLifecycleAndConflict(test *testing.T) {
	path := createLogs(test)
	if changed, err := changeTrigger(path, false); err != nil || !changed {
		test.Fatalf("%t %v", changed, err)
	}
	if changed, err := changeTrigger(path, false); err != nil || changed {
		test.Fatalf("not idempotent: %t %v", changed, err)
	}
	database, err := open(path)
	if err != nil {
		test.Fatal(err)
	}
	for _, level := range []string{"trace", "DEBUG", "Info", "WARN", "ERROR"} {
		if _, err = database.Exec("INSERT INTO logs VALUES(?,?)", level, "message"); err != nil {
			test.Fatal(err)
		}
	}
	var count int
	if err = database.QueryRow("SELECT count(*) FROM logs").Scan(&count); err != nil || count != 2 {
		test.Fatalf("filter: %d %v", count, err)
	}
	database.Close()
	if changed, err := changeTrigger(path, true); err != nil || !changed {
		test.Fatalf("%t %v", changed, err)
	}
	database, _ = open(path)
	database.Exec("CREATE TRIGGER " + triggerName + " BEFORE INSERT ON logs BEGIN SELECT 1; END")
	database.Close()
	for _, remove := range []bool{true, false} {
		if _, err = changeTrigger(path, remove); err == nil {
			test.Fatal("modified conflicting trigger")
		}
	}
}

func TestMissingDatabaseAndConfirmation(test *testing.T) {
	path := filepath.Join(test.TempDir(), "missing.sqlite")
	if _, err := changeTrigger(path, false); err == nil {
		test.Fatal("created missing DB")
	}
	if _, err := os.Stat(path); !os.IsNotExist(err) {
		test.Fatal("DB exists")
	}
	for _, answer := range []string{"", "y\n", "yes\n", " Y\n"} {
		var output bytes.Buffer
		if err := Repair(path, strings.NewReader(answer), &output); err == nil {
			test.Fatal("accepted unconfirmed maintenance")
		}
	}
}

func TestVacuumBackup(test *testing.T) {
	if runtime.GOOS == "windows" {
		test.Skip("Codex maintenance unsupported")
	}
	path := createLogs(test)
	database, _ := open(path)
	database.Exec("INSERT INTO logs VALUES('WARN','keep')")
	database.Close()
	backup, err := vacuum(path, true)
	if err != nil {
		test.Fatal(err)
	}
	database, err = open(backup)
	if err != nil {
		test.Fatal(err)
	}
	defer database.Close()
	var message string
	if err = database.QueryRow("SELECT message FROM logs").Scan(&message); err != nil || message != "keep" {
		test.Fatalf("backup: %s %v", message, err)
	}
}

func TestProcessDetection(test *testing.T) {
	for _, command := range []string{"codex", "/opt/bin/codex exec", `"/opt/with spaces/codex"`, `node --flag "/opt/with spaces/codex" exec`} {
		if !isCodexProcess(command) {
			test.Errorf("missed active Codex: %s", command)
		}
	}
	for _, command := range []string{"", "grep codex", "bash -c codex", "node app.js codex", "codex-code-mode-host", "rtk-claude-safe repair-codex-sqlite"} {
		if isCodexProcess(command) {
			test.Errorf("false positive: %s", command)
		}
	}
}

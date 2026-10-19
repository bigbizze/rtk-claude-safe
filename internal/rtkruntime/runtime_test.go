package rtkruntime

import (
	"os"
	"path/filepath"
	"runtime"
	"testing"
	"time"
)

func TestVersionPolicy(test *testing.T) {
	for _, value := range []string{"rtk 0.42.4", "rtk v0.43.0", "rtk 1.0.0\nupdate: 2.0.0", "rtk.exe 0.42.4\nrtk.exe 0.42.4"} {
		if err := Validate(value); err != nil {
			test.Errorf("%q: %v", value, err)
		}
	}
	for _, value := range []string{"", "rtk 0.42.3", "rtk 0.42.4-dev", "rtk dev-0.42.4-test", "rtk 0.42.4\nrtk v0.42.4", "update: 1.0.0", "rtk unknown", "rtk 0.42.4+meta"} {
		if err := Validate(value); err == nil {
			test.Errorf("accepted %q", value)
		}
	}
}

func TestProbeFailureAndTimeout(test *testing.T) {
	if runtime.GOOS == "windows" {
		test.Skip("Unix executable fixtures")
	}
	path := filepath.Join(test.TempDir(), "rtk")
	for _, body := range []string{"echo 'rtk 0.42.4'; exit 1", "echo 'rtk 0.42.3'", "echo 'rtk 0.42.4-rc.1'"} {
		if err := os.WriteFile(path, []byte("#!/bin/sh\n"+body+"\n"), 0755); err != nil {
			test.Fatal(err)
		}
		if Require(path) == nil {
			test.Fatalf("accepted %q", body)
		}
	}
	if err := os.WriteFile(path, []byte("#!/bin/sh\nexec /bin/sleep 10\n"), 0755); err != nil {
		test.Fatal(err)
	}
	start := time.Now()
	if Require(path) == nil || time.Since(start) > 3*time.Second {
		test.Fatal("version probe timeout failed")
	}
}

package rtkruntime

import (
	"bytes"
	"context"
	"fmt"
	"os/exec"
	"regexp"
	"strconv"
	"strings"
	"time"
)

const MinVersion = "0.42.4"

var versionPattern = regexp.MustCompile(`^(dev-)?v?([0-9]+)\.([0-9]+)\.([0-9]+)(-.+)?$`)

func Validate(output string) error {
	token := ""
	for _, line := range strings.Split(output, "\n") {
		fields := strings.Fields(line)
		if len(fields) < 2 || (fields[0] != "rtk" && fields[0] != "rtk.exe") {
			continue
		}
		if token != "" && token != fields[1] {
			return fmt.Errorf("conflicting RTK version lines")
		}
		token = fields[1]
	}
	match := versionPattern.FindStringSubmatch(token)
	if match == nil || match[1] != "" || match[5] != "" {
		return fmt.Errorf("unsupported RTK version %q; requires stable >= %s", token, MinVersion)
	}
	minimum := []int{0, 42, 4}
	for index, raw := range match[2:5] {
		value, err := strconv.Atoi(raw)
		if err != nil {
			return err
		}
		if value > minimum[index] {
			return nil
		}
		if value < minimum[index] {
			return fmt.Errorf("RTK %s is older than %s", token, MinVersion)
		}
	}
	return nil
}

func Require(path string) error {
	ctx, cancel := context.WithTimeout(context.Background(), time.Second)
	defer cancel()
	command := exec.CommandContext(ctx, path, "--version")
	command.WaitDelay = 100 * time.Millisecond
	var stdout, stderr bytes.Buffer
	command.Stdout, command.Stderr = &stdout, &stderr
	if err := command.Run(); err != nil {
		return fmt.Errorf("RTK version probe: %w", err)
	}
	return Validate(stdout.String() + "\n" + stderr.String())
}

func Supported() bool {
	path, err := exec.LookPath("rtk")
	return err == nil && Require(path) == nil
}

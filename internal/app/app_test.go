package app

import (
	"bytes"
	"strings"
	"testing"
)

func TestInformationalAndInvalidCommands(test *testing.T) {
	for _, testCase := range []struct {
		args []string
		code int
	}{
		{[]string{"--version"}, 0},
		{[]string{"--help"}, 0},
		{nil, 2},
		{[]string{"unknown"}, 2},
		{[]string{"init", "--unknown"}, 2},
		{[]string{"enforce-subagent-depth", "extra"}, 2},
	} {
		var output, diagnostic bytes.Buffer
		if code := Run(testCase.args, strings.NewReader(""), &output, &diagnostic); code != testCase.code {
			test.Fatalf("%v: code %d, want %d: %s", testCase.args, code, testCase.code, diagnostic.String())
		}
		if strings.Contains(output.String(), "claude-hook") {
			test.Fatal("internal hook command leaked into public help")
		}
	}
}

func TestInvalidHookInputDoesNotInvokeRTK(test *testing.T) {
	test.Setenv("PATH", test.TempDir())
	for _, command := range []string{"claude-hook", "codex-hook"} {
		for _, input := range []string{"", "{", "null", "[]", "{}", `{"tool_name":"Read"}`} {
			var output, diagnostic bytes.Buffer
			if code := Run([]string{command}, strings.NewReader(input), &output, &diagnostic); code != 0 {
				test.Fatalf("%s %q: %d %s", command, input, code, diagnostic.String())
			}
			if output.Len() != 0 {
				test.Fatalf("%s %q emitted %s", command, input, output.String())
			}
		}
	}
}

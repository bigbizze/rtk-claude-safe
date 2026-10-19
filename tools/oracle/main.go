package main

import (
	"bufio"
	"bytes"
	"encoding/json"
	"fmt"
	"os"

	"github.com/bigbizze/rtk-claude-safe/internal/hook"
	"github.com/bigbizze/rtk-claude-safe/internal/policy"
)

func main() {
	scanner := bufio.NewScanner(os.Stdin)
	scanner.Buffer(make([]byte, 4096), 4*1024*1024)
	writer := bufio.NewWriter(os.Stdout)
	defer writer.Flush()
	encoder := json.NewEncoder(writer)
	for scanner.Scan() {
		var request struct {
			Command   string `json:"command"`
			Agent     string `json:"agent"`
			Payload   any    `json:"payload"`
			Supported bool   `json:"supported"`
			Kind      string `json:"kind"`
		}
		decoder := json.NewDecoder(bytes.NewReader(scanner.Bytes()))
		decoder.UseNumber()
		if err := decoder.Decode(&request); err != nil {
			fmt.Fprintln(os.Stderr, err)
			os.Exit(1)
		}
		var result any
		if request.Kind == "payload" {
			result = hook.RewritePayload(request.Payload, request.Agent, func() bool { return request.Supported })
		} else if rewritten, ok := policy.Rewrite(request.Command, request.Agent); ok {
			result = rewritten
		}
		if err := encoder.Encode(result); err != nil {
			fmt.Fprintln(os.Stderr, err)
			os.Exit(1)
		}
	}
	if err := scanner.Err(); err != nil {
		fmt.Fprintln(os.Stderr, err)
		os.Exit(1)
	}
}

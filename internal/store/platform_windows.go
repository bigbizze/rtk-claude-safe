package store

import "fmt"

func Lock(path string, exclusive bool) (func(), error) {
	return nil, fmt.Errorf("native Windows Codex hooks are not supported")
}
func FreeSpace(path string) (uint64, error) {
	return 0, fmt.Errorf("native Windows Codex maintenance is not supported")
}

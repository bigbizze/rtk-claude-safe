//go:build !windows

package store

import (
	"os"

	"golang.org/x/sys/unix"
)

func Lock(path string, exclusive bool) (func(), error) {
	file, err := os.OpenFile(path, os.O_CREATE|os.O_RDWR, 0600)
	if err != nil {
		return nil, err
	}
	operation := unix.LOCK_SH
	if exclusive {
		operation = unix.LOCK_EX
	}
	if err = unix.Flock(int(file.Fd()), operation); err != nil {
		file.Close()
		return nil, err
	}
	return func() { unix.Flock(int(file.Fd()), unix.LOCK_UN); file.Close() }, nil
}

func FreeSpace(path string) (uint64, error) {
	var info unix.Statfs_t
	err := unix.Statfs(path, &info)
	return uint64(info.Bavail) * uint64(info.Bsize), err
}

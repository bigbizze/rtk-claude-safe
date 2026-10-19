package install

import (
	"archive/tar"
	"archive/zip"
	"bytes"
	"compress/gzip"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"os"
	"os/exec"
	"path/filepath"
	"runtime"
	"strings"
	"time"

	"github.com/bigbizze/rtk-claude-safe/internal/rtkruntime"
)

type asset struct {
	Name   string `json:"name"`
	URL    string `json:"browser_download_url"`
	Digest string `json:"digest"`
}

type release struct {
	Tag        string  `json:"tag_name"`
	Draft      *bool   `json:"draft"`
	Prerelease *bool   `json:"prerelease"`
	Assets     []asset `json:"assets"`
}

func target(system, arch string) (string, string, error) {
	if arch == "amd64" {
		arch = "x86_64"
	} else if arch == "arm64" {
		arch = "aarch64"
	} else {
		return "", "", fmt.Errorf("unsupported architecture: %s", arch)
	}
	switch system {
	case "linux":
		if arch == "x86_64" {
			return arch + "-unknown-linux-musl", "tar.gz", nil
		}
		return arch + "-unknown-linux-gnu", "tar.gz", nil
	case "darwin":
		return arch + "-apple-darwin", "tar.gz", nil
	case "windows":
		if arch == "x86_64" {
			return arch + "-pc-windows-msvc", "zip", nil
		}
	}
	return "", "", fmt.Errorf("unsupported platform: %s/%s", system, arch)
}

func download(client *http.Client, url string) ([]byte, error) {
	request, err := http.NewRequest(http.MethodGet, url, nil)
	if err != nil {
		return nil, err
	}
	request.Header.Set("User-Agent", "rtk-claude-safe")
	response, err := client.Do(request)
	if err != nil {
		return nil, err
	}
	defer response.Body.Close()
	if response.StatusCode != http.StatusOK {
		return nil, fmt.Errorf("download %s: HTTP %d", url, response.StatusCode)
	}
	return io.ReadAll(response.Body)
}

func digest(value string) (string, error) {
	algorithm, hash, ok := strings.Cut(value, ":")
	decoded, err := hex.DecodeString(hash)
	if !ok || !strings.EqualFold(algorithm, "sha256") || err != nil || len(decoded) != 32 {
		return "", fmt.Errorf("invalid SHA-256 digest %q", value)
	}
	return strings.ToLower(hash), nil
}

func verify(data []byte, hash string) error {
	actual := fmt.Sprintf("%x", sha256.Sum256(data))
	if actual != hash {
		return fmt.Errorf("SHA-256 mismatch: expected %s, got %s", hash, actual)
	}
	return nil
}

func uniqueAsset(assets []asset, name string) (asset, error) {
	var found asset
	count := 0
	for _, candidate := range assets {
		if candidate.Name == name {
			found = candidate
			count++
		}
	}
	if count != 1 || found.URL == "" {
		return found, fmt.Errorf("expected exactly one release asset %s", name)
	}
	hash, err := digest(found.Digest)
	found.Digest = hash
	return found, err
}

func verifiedArchive(client *http.Client, url, targetName, extension string) ([]byte, error) {
	raw, err := download(client, url)
	if err != nil {
		return nil, err
	}
	var metadata release
	if err = json.Unmarshal(raw, &metadata); err != nil {
		return nil, err
	}
	if metadata.Draft == nil || metadata.Prerelease == nil || *metadata.Draft || *metadata.Prerelease {
		return nil, fmt.Errorf("release is not stable")
	}
	if err = rtkruntime.Validate("rtk " + metadata.Tag); err != nil {
		return nil, err
	}
	name := "rtk-" + targetName + "." + extension
	archive, err := uniqueAsset(metadata.Assets, name)
	if err != nil {
		return nil, err
	}
	checksums, err := uniqueAsset(metadata.Assets, "checksums.txt")
	if err != nil {
		return nil, err
	}
	checksumData, err := download(client, checksums.URL)
	if err != nil {
		return nil, err
	}
	if err = verify(checksumData, checksums.Digest); err != nil {
		return nil, err
	}
	count := 0
	for _, line := range strings.Split(string(checksumData), "\n") {
		fields := strings.Fields(line)
		if len(fields) == 2 && fields[1] == name {
			count++
			if strings.ToLower(fields[0]) != archive.Digest {
				return nil, fmt.Errorf("release API and checksums disagree")
			}
		}
	}
	if count != 1 {
		return nil, fmt.Errorf("expected exactly one checksum for %s", name)
	}
	data, err := download(client, archive.URL)
	if err != nil {
		return nil, err
	}
	if err = verify(data, archive.Digest); err != nil {
		return nil, err
	}
	return data, nil
}

func extract(data []byte, extension, name string) ([]byte, error) {
	var result []byte
	count := 0
	switch extension {
	case "tar.gz":
		compressed, err := gzip.NewReader(bytes.NewReader(data))
		if err != nil {
			return nil, err
		}
		defer compressed.Close()
		reader := tar.NewReader(compressed)
		for {
			header, err := reader.Next()
			if err == io.EOF {
				break
			}
			if err != nil {
				return nil, err
			}
			if header.Name == name && (header.Typeflag == tar.TypeReg || header.Typeflag == tar.TypeRegA) {
				count++
				result, err = io.ReadAll(reader)
				if err != nil {
					return nil, err
				}
			}
		}
	case "zip":
		reader, err := zip.NewReader(bytes.NewReader(data), int64(len(data)))
		if err != nil {
			return nil, err
		}
		for _, entry := range reader.File {
			if entry.Name == name && entry.Mode().IsRegular() {
				count++
				stream, err := entry.Open()
				if err != nil {
					return nil, err
				}
				result, err = io.ReadAll(stream)
				stream.Close()
				if err != nil {
					return nil, err
				}
			}
		}
	default:
		return nil, fmt.Errorf("unsupported archive: %s", extension)
	}
	if count != 1 {
		return nil, fmt.Errorf("expected exactly one regular %s in archive", name)
	}
	return result, nil
}

func Ensure(installDir string, stderr io.Writer) (string, error) {
	if existing, err := exec.LookPath("rtk"); err == nil {
		return existing, rtkruntime.Require(existing)
	}
	if strings.HasPrefix(installDir, "~/") {
		home, err := os.UserHomeDir()
		if err != nil {
			return "", err
		}
		installDir = filepath.Join(home, installDir[2:])
	}
	name := "rtk"
	if runtime.GOOS == "windows" {
		name += ".exe"
	}
	destination := filepath.Join(installDir, name)
	if _, err := os.Stat(destination); os.IsNotExist(err) {
		targetName, extension, err := target(runtime.GOOS, runtime.GOARCH)
		if err != nil {
			return "", err
		}
		archive, err := verifiedArchive(&http.Client{Timeout: 120 * time.Second}, "https://api.github.com/repos/rtk-ai/rtk/releases/latest", targetName, extension)
		if err != nil {
			return "", err
		}
		binary, err := extract(archive, extension, name)
		if err != nil {
			return "", err
		}
		if err = os.MkdirAll(installDir, 0755); err != nil {
			return "", err
		}
		pattern := ".rtk-*"
		if runtime.GOOS == "windows" {
			pattern += ".exe"
		}
		temporary, err := os.CreateTemp(installDir, pattern)
		if err != nil {
			return "", err
		}
		defer os.Remove(temporary.Name())
		_, writeErr := temporary.Write(binary)
		closeErr := temporary.Close()
		if writeErr != nil {
			return "", writeErr
		}
		if closeErr != nil {
			return "", closeErr
		}
		if err = os.Chmod(temporary.Name(), 0755); err != nil {
			return "", err
		}
		if err = rtkruntime.Require(temporary.Name()); err != nil {
			return "", err
		}
		if err = os.Rename(temporary.Name(), destination); err != nil {
			return "", err
		}
		fmt.Fprintln(stderr, "[rtk-claude-safe] installed", destination)
	}
	if err := rtkruntime.Require(destination); err != nil {
		return "", err
	}
	found, err := exec.LookPath("rtk")
	if err != nil {
		return "", fmt.Errorf("%s is not on PATH", installDir)
	}
	resolved, _ := filepath.EvalSymlinks(found)
	selected, _ := filepath.EvalSymlinks(destination)
	resolved, _ = filepath.Abs(resolved)
	selected, _ = filepath.Abs(selected)
	if resolved != selected && !(runtime.GOOS == "windows" && strings.EqualFold(resolved, selected)) {
		return "", fmt.Errorf("PATH resolves RTK to %s instead of %s", found, destination)
	}
	return found, nil
}

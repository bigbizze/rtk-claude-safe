package install

import (
	"archive/tar"
	"archive/zip"
	"bytes"
	"compress/gzip"
	"crypto/sha256"
	"encoding/json"
	"fmt"
	"net/http"
	"net/http/httptest"
	"os"
	"testing"
)

func TestArchiveExtractionRejectsUnsafeMembers(test *testing.T) {
	for _, testCase := range []struct {
		name   string
		kind   byte
		copies int
		valid  bool
	}{
		{"rtk", tar.TypeReg, 1, true}, {"rtk", tar.TypeReg, 2, false}, {"../rtk", tar.TypeReg, 1, false}, {"/rtk", tar.TypeReg, 1, false}, {"rtk", tar.TypeSymlink, 1, false}, {"rtk", tar.TypeLink, 1, false},
	} {
		var buffer bytes.Buffer
		compressed := gzip.NewWriter(&buffer)
		archive := tar.NewWriter(compressed)
		for index := 0; index < testCase.copies; index++ {
			header := &tar.Header{Name: testCase.name, Typeflag: testCase.kind, Mode: 0755}
			if testCase.kind == tar.TypeReg {
				header.Size = 3
			}
			if err := archive.WriteHeader(header); err != nil {
				test.Fatal(err)
			}
			if testCase.kind == tar.TypeReg {
				archive.Write([]byte("bin"))
			}
		}
		archive.Close()
		compressed.Close()
		result, err := extract(buffer.Bytes(), "tar.gz", "rtk")
		if (err == nil) != testCase.valid {
			test.Fatalf("%+v: %v", testCase, err)
		}
		if testCase.valid && string(result) != "bin" {
			test.Fatal(string(result))
		}
	}
	var buffer bytes.Buffer
	archive := zip.NewWriter(&buffer)
	header := &zip.FileHeader{Name: "rtk.exe"}
	header.SetMode(os.ModeSymlink | 0777)
	entry, err := archive.CreateHeader(header)
	if err != nil {
		test.Fatal(err)
	}
	entry.Write([]byte("target"))
	archive.Close()
	if _, err = extract(buffer.Bytes(), "zip", "rtk.exe"); err == nil {
		test.Fatal("accepted zip symlink")
	}
}

func TestReleaseIntegrity(test *testing.T) {
	for _, failure := range []string{"", "missing-digest", "checksum-corrupt", "archive-corrupt", "disagree", "duplicate", "prerelease"} {
		test.Run(failure, func(test *testing.T) {
			archive := []byte("test archive")
			hash := fmt.Sprintf("%x", sha256.Sum256(archive))
			name := "rtk-x86_64-unknown-linux-musl.tar.gz"
			checksums := []byte(hash + "  " + name + "\n")
			checksumHash := fmt.Sprintf("%x", sha256.Sum256(checksums))
			var server *httptest.Server
			server = httptest.NewServer(http.HandlerFunc(func(writer http.ResponseWriter, request *http.Request) {
				switch request.URL.Path {
				case "/latest":
					archiveDigest := "sha256:" + hash
					if failure == "missing-digest" {
						archiveDigest = ""
					}
					if failure == "disagree" {
						archiveDigest = "sha256:" + checksumHash
					}
					assets := []asset{{Name: name, URL: server.URL + "/archive", Digest: archiveDigest}, {Name: "checksums.txt", URL: server.URL + "/checksums", Digest: "sha256:" + checksumHash}}
					if failure == "duplicate" {
						assets = append(assets, assets[0])
					}
					json.NewEncoder(writer).Encode(map[string]any{"tag_name": "v0.42.4", "draft": false, "prerelease": failure == "prerelease", "assets": assets})
				case "/checksums":
					if failure == "checksum-corrupt" {
						writer.Write([]byte("bad"))
					} else {
						writer.Write(checksums)
					}
				case "/archive":
					if failure == "archive-corrupt" {
						writer.Write([]byte("bad"))
					} else {
						writer.Write(archive)
					}
				default:
					writer.WriteHeader(404)
				}
			}))
			defer server.Close()
			actual, err := verifiedArchive(server.Client(), server.URL+"/latest", "x86_64-unknown-linux-musl", "tar.gz")
			if (err == nil) != (failure == "") {
				test.Fatalf("%s: %v", failure, err)
			}
			if failure == "" && !bytes.Equal(actual, archive) {
				test.Fatal("wrong archive")
			}
		})
	}
}

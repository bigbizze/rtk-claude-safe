package store

import (
	"database/sql"
	"net/url"
	"path/filepath"
	"strconv"

	_ "modernc.org/sqlite"
)

func Open(path, mode string, timeout int) (*sql.DB, error) {
	absolute, err := filepath.Abs(path)
	if err != nil {
		return nil, err
	}
	location := url.URL{Scheme: "file", Path: filepath.ToSlash(absolute)}
	query := url.Values{"mode": []string{mode}, "_pragma": []string{"busy_timeout(" + strconv.Itoa(timeout) + ")"}}
	location.RawQuery = query.Encode()
	database, err := sql.Open("sqlite", location.String())
	if err != nil {
		return nil, err
	}
	database.SetMaxOpenConns(1)
	if err = database.Ping(); err != nil {
		database.Close()
		return nil, err
	}
	return database, nil
}

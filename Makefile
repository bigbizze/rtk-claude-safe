.PHONY: build test differential install

build:
	go build -trimpath -o build/rtk-claude-safe .

test:
	go test ./...

differential:
	python3 tools/differential.py
	python3 tools/stateful_differential.py

install:
	go install .

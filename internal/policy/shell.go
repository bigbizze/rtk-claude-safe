package policy

import (
	"regexp"
	"strings"
)

var bareWord = regexp.MustCompile(`^[a-zA-Z0-9_@%+=:,./-]+$`)

func shellJoin(parts []string) string {
	quoted := make([]string, len(parts))
	for index, part := range parts {
		if bareWord.MatchString(part) {
			quoted[index] = part
		} else {
			quoted[index] = "'" + strings.ReplaceAll(part, "'", "'\"'\"'") + "'"
		}
	}
	return strings.Join(quoted, " ")
}

func splitWords(command string) ([]string, bool) {
	var parts []string
	var current strings.Builder
	var quote byte
	started := false
	for index := 0; index < len(command); index++ {
		char := command[index]
		if quote == '\'' {
			if char == quote {
				quote = 0
			} else {
				current.WriteByte(char)
			}
			continue
		}
		if char == '\\' {
			if index+1 == len(command) {
				return nil, false
			}
			next := command[index+1]
			if quote == '"' && next != '"' && next != '\\' {
				current.WriteByte(char)
			}
			current.WriteByte(next)
			started = true
			index++
			continue
		}
		if quote != 0 {
			if char == quote {
				quote = 0
			} else {
				current.WriteByte(char)
			}
			continue
		}
		if char == '\'' || char == '"' {
			quote = char
			started = true
			continue
		}
		if strings.ContainsRune(" \t\r\n", rune(char)) {
			if started {
				parts = append(parts, current.String())
				current.Reset()
				started = false
			}
			continue
		}
		started = true
		current.WriteByte(char)
	}
	if quote != 0 {
		return nil, false
	}
	if started {
		parts = append(parts, current.String())
	}
	return parts, len(parts) > 0
}

func splitList(command string) ([]string, []string, bool) {
	command = trim(command)
	if command == "" {
		return nil, nil, false
	}
	var segments, separators []string
	var quote byte
	escaped, start := false, 0
	for index := 0; index < len(command); index++ {
		char := command[index]
		if escaped {
			escaped = false
			continue
		}
		if quote != 0 {
			if char == quote {
				quote = 0
			} else if quote == '"' && char == '\\' {
				escaped = true
			}
			continue
		}
		if char == '\\' {
			escaped = true
			continue
		}
		if char == '\'' || char == '"' {
			quote = char
			continue
		}
		if strings.ContainsRune("#<>()", rune(char)) {
			return nil, nil, false
		}
		if char == ';' || char == '&' || char == '|' {
			width := 1
			if char != ';' {
				if index+1 >= len(command) || command[index+1] != char || (index+2 < len(command) && command[index+2] == char) {
					return nil, nil, false
				}
				width = 2
			}
			segment := trim(command[start:index])
			if segment == "" {
				return nil, nil, false
			}
			segments = append(segments, segment)
			separators = append(separators, command[index:index+width])
			index += width - 1
			start = index + 1
		}
	}
	last := trim(command[start:])
	if quote != 0 || escaped || last == "" {
		return nil, nil, false
	}
	return append(segments, last), separators, true
}

package policy

import (
	"regexp"
	"strconv"
	"strings"
	"unicode"
)

var decimalInteger = regexp.MustCompile(`^[+-]?[0-9](?:_?[0-9])*$`)

func lower(value string) string {
	return strings.ToLower(strings.ReplaceAll(value, "İ", "i\u0307"))
}

func trim(value string) string {
	return strings.TrimFunc(value, func(char rune) bool { return unicode.IsSpace(char) || char >= 0x1c && char <= 0x1f })
}

func smallInteger(value string) bool {
	value = strings.Map(func(char rune) rune {
		for _, interval := range unicode.Digit.R16 {
			if char >= rune(interval.Lo) && char <= rune(interval.Hi) && (char-rune(interval.Lo))%rune(interval.Stride) == 0 {
				return '0' + (char-rune(interval.Lo))/rune(interval.Stride)%10
			}
		}
		for _, interval := range unicode.Digit.R32 {
			if char >= rune(interval.Lo) && char <= rune(interval.Hi) && (char-rune(interval.Lo))%rune(interval.Stride) == 0 {
				return '0' + (char-rune(interval.Lo))/rune(interval.Stride)%10
			}
		}
		return char
	}, strings.TrimSpace(value))
	if !decimalInteger.MatchString(value) {
		return false
	}
	number, err := strconv.Atoi(strings.ReplaceAll(value, "_", ""))
	return err == nil && number >= 1 && number <= 50
}

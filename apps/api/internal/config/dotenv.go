package config

import (
	"bufio"
	"fmt"
	"os"
	"strings"
)

// LoadDotenv reads a `.env`-style file and sets any variable that is not
// already present in the process environment. Real environment variables
// always win, so Docker Compose / CI can override the file without editing it.
//
// A missing file is not an error: in a container the values normally arrive as
// real environment variables and no file exists. A malformed file *is* an
// error — a silently ignored typo in a DSN or an API key is exactly the class
// of failure NFR5.6 tells us to surface loudly.
func LoadDotenv(path string) error {
	f, err := os.Open(path)
	if err != nil {
		if os.IsNotExist(err) {
			return nil
		}
		return fmt.Errorf("open %s: %w", path, err)
	}
	defer f.Close()

	scanner := bufio.NewScanner(f)
	lineNo := 0
	for scanner.Scan() {
		lineNo++
		line := strings.TrimSpace(scanner.Text())
		if line == "" || strings.HasPrefix(line, "#") {
			continue
		}
		line = strings.TrimPrefix(line, "export ")

		key, value, found := strings.Cut(line, "=")
		if !found {
			return fmt.Errorf("%s:%d: expected KEY=VALUE, got %q", path, lineNo, line)
		}
		key = strings.TrimSpace(key)
		if key == "" {
			return fmt.Errorf("%s:%d: empty key", path, lineNo)
		}
		value = unquote(strings.TrimSpace(value))

		if _, exists := os.LookupEnv(key); exists {
			continue
		}
		if err := os.Setenv(key, value); err != nil {
			return fmt.Errorf("%s:%d: set %s: %w", path, lineNo, key, err)
		}
	}
	if err := scanner.Err(); err != nil {
		return fmt.Errorf("read %s: %w", path, err)
	}
	return nil
}

// unquote strips one layer of matching single or double quotes. Unquoted
// values keep everything after the first '=' verbatim, including '#', because
// Postgres passwords and DSNs legitimately contain that character.
func unquote(v string) string {
	if len(v) >= 2 {
		if (v[0] == '"' && v[len(v)-1] == '"') || (v[0] == '\'' && v[len(v)-1] == '\'') {
			return v[1 : len(v)-1]
		}
	}
	return v
}

package config

import (
	"os"
	"path/filepath"
	"strings"
	"testing"
)

// clearEnv removes every variable Load reads so each case starts from a known
// state regardless of the developer's shell or CI environment.
func clearEnv(t *testing.T) {
	t.Helper()
	for _, k := range []string{"API_PORT", "DATABASE_URL", "QUANT_MCP_URL", "LOG_LEVEL"} {
		t.Setenv(k, "")
		os.Unsetenv(k)
	}
}

func TestLoadRequiresDatabaseAndQuantURL(t *testing.T) {
	tests := []struct {
		name    string
		env     map[string]string
		wantErr string
	}{
		{
			name:    "missing database url",
			env:     map[string]string{"QUANT_MCP_URL": "http://quant-mcp:8000"},
			wantErr: "DATABASE_URL is required",
		},
		{
			name:    "missing quant url",
			env:     map[string]string{"DATABASE_URL": "postgres://x/y"},
			wantErr: "QUANT_MCP_URL is required",
		},
		{
			name: "bad log level",
			env: map[string]string{
				"DATABASE_URL": "postgres://x/y", "QUANT_MCP_URL": "http://q:8000", "LOG_LEVEL": "chatty",
			},
			wantErr: "LOG_LEVEL must be one of",
		},
		{
			name: "non numeric port",
			env: map[string]string{
				"DATABASE_URL": "postgres://x/y", "QUANT_MCP_URL": "http://q:8000", "API_PORT": "eighty",
			},
			wantErr: "API_PORT must be an integer",
		},
		{
			name: "out of range port",
			env: map[string]string{
				"DATABASE_URL": "postgres://x/y", "QUANT_MCP_URL": "http://q:8000", "API_PORT": "70000",
			},
			wantErr: "API_PORT must be 1-65535",
		},
	}

	for _, tc := range tests {
		t.Run(tc.name, func(t *testing.T) {
			clearEnv(t)
			for k, v := range tc.env {
				t.Setenv(k, v)
			}
			_, err := Load()
			if err == nil {
				t.Fatalf("expected an error mentioning %q, got nil", tc.wantErr)
			}
			if !strings.Contains(err.Error(), tc.wantErr) {
				t.Fatalf("error %q does not mention %q", err, tc.wantErr)
			}
		})
	}
}

func TestLoadValidConfig(t *testing.T) {
	clearEnv(t)
	t.Setenv("DATABASE_URL", "postgres://quant:pw@db:5432/quant?sslmode=disable")
	t.Setenv("QUANT_MCP_URL", "http://quant-mcp:8000/")
	t.Setenv("API_PORT", "9090")

	cfg, err := Load()
	if err != nil {
		t.Fatalf("Load: %v", err)
	}
	if cfg.Port != 9090 {
		t.Errorf("Port = %d, want 9090", cfg.Port)
	}
	// The trailing slash must be trimmed or every downstream URL would end up
	// with a double slash.
	if cfg.QuantMCPURL != "http://quant-mcp:8000" {
		t.Errorf("QuantMCPURL = %q, want trailing slash trimmed", cfg.QuantMCPURL)
	}
	if cfg.LogLevel != "info" {
		t.Errorf("LogLevel = %q, want the default %q", cfg.LogLevel, "info")
	}
}

func TestLoadDotenvSeedsMissingValuesOnly(t *testing.T) {
	clearEnv(t)
	dir := t.TempDir()
	path := filepath.Join(dir, ".env")
	content := strings.Join([]string{
		"# a comment",
		"",
		"export LOG_LEVEL=debug",
		`DATABASE_URL="postgres://quant:p#ssw0rd@db:5432/quant?sslmode=disable"`,
		"QUANT_MCP_URL=http://quant-mcp:8000",
	}, "\n")
	if err := os.WriteFile(path, []byte(content), 0o600); err != nil {
		t.Fatal(err)
	}

	// A real environment variable must win over the file.
	t.Setenv("LOG_LEVEL", "warn")

	cfg, err := Load(path)
	if err != nil {
		t.Fatalf("Load: %v", err)
	}
	if cfg.LogLevel != "warn" {
		t.Errorf("LogLevel = %q, want the environment to override the file", cfg.LogLevel)
	}
	// '#' inside a quoted password must survive: treating it as a comment
	// would silently produce a wrong DSN.
	want := "postgres://quant:p#ssw0rd@db:5432/quant?sslmode=disable"
	if cfg.DatabaseURL != want {
		t.Errorf("DatabaseURL = %q, want %q", cfg.DatabaseURL, want)
	}
}

func TestLoadDotenvRejectsMalformedLine(t *testing.T) {
	clearEnv(t)
	dir := t.TempDir()
	path := filepath.Join(dir, ".env")
	if err := os.WriteFile(path, []byte("THIS_LINE_HAS_NO_EQUALS\n"), 0o600); err != nil {
		t.Fatal(err)
	}
	if err := LoadDotenv(path); err == nil {
		t.Fatal("expected malformed .env to be rejected, got nil")
	}
}

func TestLoadDotenvMissingFileIsNotAnError(t *testing.T) {
	if err := LoadDotenv(filepath.Join(t.TempDir(), "absent.env")); err != nil {
		t.Fatalf("a missing .env must be tolerated (containers pass real env vars): %v", err)
	}
}

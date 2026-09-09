// Package config loads the API service's runtime configuration from the
// environment (optionally seeded from a .env file) and validates it before the
// server starts. Invalid configuration is a startup failure, never a runtime
// surprise (NFR5.6).
package config

import (
	"errors"
	"fmt"
	"os"
	"strconv"
	"strings"
	"time"
)

// Config is the fully resolved, validated configuration for the Go API.
type Config struct {
	// Port the HTTP server listens on.
	Port int
	// DatabaseURL is the PostgreSQL DSN. Required.
	DatabaseURL string
	// QuantMCPURL is the base URL of the Python quant-mcp service, reached
	// over HTTP/JSON (planning.md §4 decision 1). Required.
	QuantMCPURL string
	// LogLevel is one of debug|info|warn|error.
	LogLevel string
	// RequestTimeout bounds an inbound request's total handling time.
	RequestTimeout time.Duration
	// QuantMCPTimeout bounds a single outbound call to quant-mcp. Backtests
	// are slow, so this is deliberately much larger than RequestTimeout's
	// default would suggest.
	QuantMCPTimeout time.Duration
}

// Load reads configuration from the environment, seeding it from `.env` files
// at the given paths first (earlier paths win; already-set environment
// variables always win over both).
func Load(dotenvPaths ...string) (*Config, error) {
	for _, p := range dotenvPaths {
		if err := LoadDotenv(p); err != nil {
			return nil, err
		}
	}

	cfg := &Config{
		LogLevel:        getenv("LOG_LEVEL", "info"),
		DatabaseURL:     os.Getenv("DATABASE_URL"),
		QuantMCPURL:     strings.TrimRight(getenv("QUANT_MCP_URL", ""), "/"),
		RequestTimeout:  120 * time.Second,
		QuantMCPTimeout: 110 * time.Second,
	}

	port, err := getenvInt("API_PORT", 8080)
	if err != nil {
		return nil, err
	}
	cfg.Port = port

	if err := cfg.validate(); err != nil {
		return nil, err
	}
	return cfg, nil
}

func (c *Config) validate() error {
	var problems []string

	if c.Port < 1 || c.Port > 65535 {
		problems = append(problems, fmt.Sprintf("API_PORT must be 1-65535, got %d", c.Port))
	}
	if c.DatabaseURL == "" {
		problems = append(problems, "DATABASE_URL is required (the API refuses to start without a database)")
	}
	if c.QuantMCPURL == "" {
		problems = append(problems, "QUANT_MCP_URL is required (the API cannot run a backtest without the quant service)")
	}
	switch c.LogLevel {
	case "debug", "info", "warn", "error":
	default:
		problems = append(problems, fmt.Sprintf("LOG_LEVEL must be one of debug|info|warn|error, got %q", c.LogLevel))
	}

	if len(problems) > 0 {
		return fmt.Errorf("invalid configuration:\n  - %s", strings.Join(problems, "\n  - "))
	}
	return nil
}

func getenv(key, fallback string) string {
	if v, ok := os.LookupEnv(key); ok && v != "" {
		return v
	}
	return fallback
}

func getenvInt(key string, fallback int) (int, error) {
	raw, ok := os.LookupEnv(key)
	if !ok || raw == "" {
		return fallback, nil
	}
	n, err := strconv.Atoi(raw)
	if err != nil {
		return 0, fmt.Errorf("invalid configuration: %s must be an integer, got %q", key, raw)
	}
	return n, nil
}

// ErrNotConfigured is returned by components that were deliberately left
// unconfigured, so callers can distinguish "off" from "broken".
var ErrNotConfigured = errors.New("not configured")

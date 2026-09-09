// Package logging provides the API's structured JSON logger and the
// request-scoped tracing helpers required by maintenance-operations.md §2:
// every meaningful request carries a request_id, and per-stage latencies are
// logged separately rather than as one aggregate number.
package logging

import (
	"context"
	"crypto/rand"
	"encoding/hex"
	"log/slog"
	"os"
	"time"
)

type contextKey string

const requestIDKey contextKey = "request_id"

// New returns a JSON logger writing to stdout, so `docker compose logs`
// captures structured lines without any external observability service.
func New(level string) *slog.Logger {
	var lvl slog.Level
	switch level {
	case "debug":
		lvl = slog.LevelDebug
	case "warn":
		lvl = slog.LevelWarn
	case "error":
		lvl = slog.LevelError
	default:
		lvl = slog.LevelInfo
	}
	handler := slog.NewJSONHandler(os.Stdout, &slog.HandlerOptions{Level: lvl})
	return slog.New(handler)
}

// NewRequestID returns a short random identifier used to correlate every log
// line, downstream call, and experiment produced by one inbound request
// (NFR2).
func NewRequestID() string {
	var b [8]byte
	if _, err := rand.Read(b[:]); err != nil {
		// A failing CSPRNG must not take down request handling; a
		// timestamp-derived ID is still unique enough to correlate logs.
		return "req_" + time.Now().UTC().Format("20060102150405.000000000")
	}
	return "req_" + hex.EncodeToString(b[:])
}

// WithRequestID stores a request ID on the context.
func WithRequestID(ctx context.Context, id string) context.Context {
	return context.WithValue(ctx, requestIDKey, id)
}

// RequestID reads the request ID back, returning "" when absent.
func RequestID(ctx context.Context) string {
	if v, ok := ctx.Value(requestIDKey).(string); ok {
		return v
	}
	return ""
}

// FromContext returns a logger already tagged with the context's request ID.
func FromContext(ctx context.Context, base *slog.Logger) *slog.Logger {
	if id := RequestID(ctx); id != "" {
		return base.With("request_id", id)
	}
	return base
}

// Stage logs one pipeline stage's latency on its own line. Callers use it as
//
//	defer logging.Stage(ctx, log, "data_retrieval")()
//
// which keeps per-stage measurement (data retrieval, quant calculation, C++
// simulation, DB persistence, AI orchestration) explicit at the call site.
func Stage(ctx context.Context, log *slog.Logger, stage string) func() {
	start := time.Now()
	return func() {
		FromContext(ctx, log).Info("stage completed",
			"stage", stage,
			"duration_ms", time.Since(start).Milliseconds(),
		)
	}
}

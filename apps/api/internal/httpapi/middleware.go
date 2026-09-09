package httpapi

import (
	"log/slog"
	"net/http"
	"time"

	"github.com/anubhavjha/ai-quant-terminal/apps/api/internal/logging"
)

// requestIDHeader lets a caller supply its own correlation ID (the Next.js app
// does this so a UI action and the API work it triggers share one ID).
const requestIDHeader = "X-Request-ID"

func requestIDFromRequest(r *http.Request) string {
	if r == nil {
		return ""
	}
	return logging.RequestID(r.Context())
}

// statusRecorder captures the response status for access logging.
type statusRecorder struct {
	http.ResponseWriter
	status int
	wrote  bool
}

func (s *statusRecorder) WriteHeader(code int) {
	if !s.wrote {
		s.status = code
		s.wrote = true
	}
	s.ResponseWriter.WriteHeader(code)
}

func (s *statusRecorder) Write(b []byte) (int, error) {
	if !s.wrote {
		s.status = http.StatusOK
		s.wrote = true
	}
	return s.ResponseWriter.Write(b)
}

// WithRequestID assigns (or adopts) a request ID and echoes it back so the
// caller can quote it in a bug report.
func WithRequestID(next http.Handler) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		id := r.Header.Get(requestIDHeader)
		if id == "" {
			id = logging.NewRequestID()
		}
		w.Header().Set(requestIDHeader, id)
		next.ServeHTTP(w, r.WithContext(logging.WithRequestID(r.Context(), id)))
	})
}

// WithAccessLog emits one structured line per request, including latency.
func WithAccessLog(log *slog.Logger) func(http.Handler) http.Handler {
	return func(next http.Handler) http.Handler {
		return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
			start := time.Now()
			rec := &statusRecorder{ResponseWriter: w, status: http.StatusOK}
			next.ServeHTTP(rec, r)
			logging.FromContext(r.Context(), log).Info("http request",
				"method", r.Method,
				"path", r.URL.Path,
				"status", rec.status,
				"duration_ms", time.Since(start).Milliseconds(),
			)
		})
	}
}

// WithRecovery converts a panic into a 500 with the standard error shape,
// logging the panic value. Without it a panic would drop the connection and
// the UI would show a network error rather than a real message.
func WithRecovery(log *slog.Logger) func(http.Handler) http.Handler {
	return func(next http.Handler) http.Handler {
		return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
			defer func() {
				if v := recover(); v != nil {
					logging.FromContext(r.Context(), log).Error("panic recovered",
						"panic", v, "path", r.URL.Path)
					WriteError(w, r, http.StatusInternalServerError, CodeInternal,
						"The API hit an unexpected internal error. The request ID below identifies this failure in the server logs.", nil)
				}
			}()
			next.ServeHTTP(w, r)
		})
	}
}

// Chain applies middleware so the first argument is the outermost layer.
func Chain(h http.Handler, mw ...func(http.Handler) http.Handler) http.Handler {
	for i := len(mw) - 1; i >= 0; i-- {
		h = mw[i](h)
	}
	return h
}

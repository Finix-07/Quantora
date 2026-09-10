package httpapi

import (
	"context"
	"encoding/json"
	"errors"
	"io"
	"log/slog"
	"net/http"
	"net/http/httptest"
	"testing"
)

// discardLogger keeps test output readable; the handlers' logging is exercised
// by their behaviour, not by inspecting the sink.
func discardLogger() *slog.Logger {
	return slog.New(slog.NewJSONHandler(io.Discard, nil))
}

func testRouter(reg *HealthRegistry) http.Handler {
	return NewRouter(RouterDeps{
		Logger: discardLogger(),
		Health: reg,
	})
}

func TestHealthzReportsOKWhenAllChecksPass(t *testing.T) {
	reg := NewHealthRegistry()
	reg.Register("process", func(context.Context) error { return nil })
	reg.Register("database", func(context.Context) error { return nil })

	rec := httptest.NewRecorder()
	testRouter(reg).ServeHTTP(rec, httptest.NewRequest(http.MethodGet, "/healthz", nil))

	if rec.Code != http.StatusOK {
		t.Fatalf("status = %d, want 200; body=%s", rec.Code, rec.Body.String())
	}
	var got HealthResponse
	if err := json.Unmarshal(rec.Body.Bytes(), &got); err != nil {
		t.Fatalf("decode: %v", err)
	}
	if got.Status != "ok" {
		t.Errorf("status = %q, want ok", got.Status)
	}
	if len(got.Dependencies) != 2 {
		t.Errorf("dependencies = %v, want both probes reported", got.Dependencies)
	}
	if got.Dependencies["database"].Status != "ok" {
		t.Errorf("database status = %+v, want ok", got.Dependencies["database"])
	}
}

func TestHealthzReports503AndNamesTheFailingDependency(t *testing.T) {
	reg := NewHealthRegistry()
	reg.Register("process", func(context.Context) error { return nil })
	reg.Register("database", func(context.Context) error { return errors.New("dial tcp db:5432: connection refused") })

	rec := httptest.NewRecorder()
	testRouter(reg).ServeHTTP(rec, httptest.NewRequest(http.MethodGet, "/healthz", nil))

	if rec.Code != http.StatusServiceUnavailable {
		t.Fatalf("status = %d, want 503", rec.Code)
	}
	var got HealthResponse
	if err := json.Unmarshal(rec.Body.Bytes(), &got); err != nil {
		t.Fatalf("decode: %v", err)
	}
	if got.Status != "degraded" {
		t.Errorf("status = %q, want degraded", got.Status)
	}
	// The runbook depends on knowing *which* dependency broke.
	if got.Dependencies["database"].Error == "" {
		t.Error("expected the database failure reason to be reported, got empty")
	}
	if got.Dependencies["process"].Status != "ok" {
		t.Error("a healthy dependency must still report ok alongside a failing one")
	}
}

func TestUnknownRouteReturnsStructuredError(t *testing.T) {
	rec := httptest.NewRecorder()
	testRouter(NewHealthRegistry()).ServeHTTP(rec, httptest.NewRequest(http.MethodGet, "/api/nope", nil))

	if rec.Code != http.StatusNotFound {
		t.Fatalf("status = %d, want 404", rec.Code)
	}
	var body struct {
		Error ErrorBody `json:"error"`
	}
	if err := json.Unmarshal(rec.Body.Bytes(), &body); err != nil {
		t.Fatalf("decode: %v", err)
	}
	if body.Error.Code != CodeNotFound {
		t.Errorf("code = %q, want %q", body.Error.Code, CodeNotFound)
	}
	if body.Error.RequestID == "" {
		t.Error("every error must carry a request_id so a user can quote it (NFR2)")
	}
}

func TestRequestIDIsEchoedAndAdopted(t *testing.T) {
	rec := httptest.NewRecorder()
	req := httptest.NewRequest(http.MethodGet, "/healthz", nil)
	req.Header.Set(requestIDHeader, "req_from_the_ui")
	testRouter(NewHealthRegistry()).ServeHTTP(rec, req)

	if got := rec.Header().Get(requestIDHeader); got != "req_from_the_ui" {
		t.Errorf("X-Request-ID = %q, want the caller's ID to be adopted", got)
	}
}

func TestPanicBecomesStructured500(t *testing.T) {
	reg := NewHealthRegistry()
	reg.Register("explodes", func(context.Context) error { panic("boom") })

	rec := httptest.NewRecorder()
	testRouter(reg).ServeHTTP(rec, httptest.NewRequest(http.MethodGet, "/healthz", nil))

	if rec.Code != http.StatusInternalServerError {
		t.Fatalf("status = %d, want 500", rec.Code)
	}
	var body struct {
		Error ErrorBody `json:"error"`
	}
	if err := json.Unmarshal(rec.Body.Bytes(), &body); err != nil {
		t.Fatalf("decode: %v", err)
	}
	if body.Error.Code != CodeInternal {
		t.Errorf("code = %q, want %q", body.Error.Code, CodeInternal)
	}
}

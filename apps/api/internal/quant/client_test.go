package quant

import (
	"context"
	"encoding/json"
	"errors"
	"io"
	"log/slog"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
	"time"

	"github.com/anubhavjha/ai-quant-terminal/apps/api/internal/logging"
)

func testClient(t *testing.T, handler http.HandlerFunc) *Client {
	t.Helper()
	server := httptest.NewServer(handler)
	t.Cleanup(server.Close)
	return New(server.URL, 5*time.Second, slog.New(slog.NewJSONHandler(io.Discard, nil)))
}

func TestRunBacktestReturnsThePayloadVerbatim(t *testing.T) {
	// The result contract is defined once, in Python. The client must not
	// re-shape it, or the API would become a second definition free to drift.
	body := `{"strategy":"macd","metrics":{"sharpe":1.5},"equity_curve":[{"timestamp":"2024-01-01","value":1.0}]}`
	client := testClient(t, func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path != "/v1/backtests" || r.Method != http.MethodPost {
			t.Errorf("unexpected request %s %s", r.Method, r.URL.Path)
		}
		w.Header().Set("Content-Type", "application/json")
		_, _ = io.WriteString(w, body)
	})

	payload, err := client.RunBacktest(context.Background(), BacktestRequest{Symbol: "X", Strategy: "macd"})
	if err != nil {
		t.Fatalf("RunBacktest: %v", err)
	}

	var decoded map[string]any
	if err := json.Unmarshal(payload, &decoded); err != nil {
		t.Fatalf("decode: %v", err)
	}
	if _, ok := decoded["equity_curve"]; !ok {
		t.Error("the payload lost fields on the way through the client")
	}
}

func TestUpstreamErrorEnvelopeIsDecoded(t *testing.T) {
	client := testClient(t, func(w http.ResponseWriter, _ *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		w.WriteHeader(http.StatusBadRequest)
		_, _ = io.WriteString(w, `{"error":{"code":"invalid_request","message":"Unknown symbol 'NOPE'. Known symbols: NIFTY","details":{"symbol":"NOPE"},"request_id":"req_1"}}`)
	})

	_, err := client.RunBacktest(context.Background(), BacktestRequest{Symbol: "NOPE"})

	var upstream *UpstreamError
	if !errors.As(err, &upstream) {
		t.Fatalf("error = %v, want an *UpstreamError", err)
	}
	if upstream.StatusCode != http.StatusBadRequest {
		t.Errorf("StatusCode = %d, want 400", upstream.StatusCode)
	}
	if upstream.Code != "invalid_request" {
		t.Errorf("Code = %q, want the engine's own code", upstream.Code)
	}
	// The engine's message is the only part a user can act on; it must survive.
	if !strings.Contains(upstream.Message, "Known symbols") {
		t.Errorf("Message = %q, want the engine's guidance preserved", upstream.Message)
	}
	if upstream.Details == nil {
		t.Error("structured details should be forwarded")
	}
}

func TestNonEnvelopeErrorBodyIsStillReported(t *testing.T) {
	client := testClient(t, func(w http.ResponseWriter, _ *http.Request) {
		w.WriteHeader(http.StatusBadGateway)
		_, _ = io.WriteString(w, "<html>nginx error</html>")
	})

	_, err := client.Universe(context.Background())

	var upstream *UpstreamError
	if !errors.As(err, &upstream) {
		t.Fatalf("error = %v, want an *UpstreamError", err)
	}
	// Discarding the body would leave the operator with nothing to diagnose.
	if !strings.Contains(upstream.Message, "nginx") {
		t.Errorf("Message = %q, want the raw body forwarded", upstream.Message)
	}
}

func TestUnreachableServiceIsDistinctFromARejectedRequest(t *testing.T) {
	// The two call for different user actions: check the stack, versus fix the
	// input.
	client := New("http://127.0.0.1:1", 500*time.Millisecond, nil)

	_, err := client.Universe(context.Background())

	if !errors.Is(err, ErrUnavailable) {
		t.Fatalf("error = %v, want ErrUnavailable", err)
	}
	var upstream *UpstreamError
	if errors.As(err, &upstream) {
		t.Error("an unreachable service must not be reported as an upstream rejection")
	}
}

func TestRequestIDIsPropagatedDownstream(t *testing.T) {
	// One user action must be traceable across web -> api -> quant-mcp (NFR2).
	var seen string
	client := testClient(t, func(w http.ResponseWriter, r *http.Request) {
		seen = r.Header.Get(requestIDHeader)
		_, _ = io.WriteString(w, `{}`)
	})

	ctx := logging.WithRequestID(context.Background(), "req_from_the_ui")
	if _, err := client.Universe(ctx); err != nil {
		t.Fatalf("Universe: %v", err)
	}

	if seen != "req_from_the_ui" {
		t.Errorf("downstream X-Request-ID = %q, want it propagated", seen)
	}
}

func TestMarketDataBuildsTheExpectedQuery(t *testing.T) {
	var path, query string
	client := testClient(t, func(w http.ResponseWriter, r *http.Request) {
		path, query = r.URL.Path, r.URL.Query().Encode()
		_, _ = io.WriteString(w, `{}`)
	})

	if _, err := client.MarketData(context.Background(), "RELIANCE.NS", "2024-01-01", "2024-06-30", "1d"); err != nil {
		t.Fatalf("MarketData: %v", err)
	}

	if path != "/v1/market/RELIANCE.NS" {
		t.Errorf("path = %q", path)
	}
	if !strings.Contains(query, "start=2024-01-01") || !strings.Contains(query, "interval=1d") {
		t.Errorf("query = %q, want start/end/interval", query)
	}
}

func TestHealthCheckFailsWhenTheServiceIsDegraded(t *testing.T) {
	client := testClient(t, func(w http.ResponseWriter, _ *http.Request) {
		w.WriteHeader(http.StatusServiceUnavailable)
		_, _ = io.WriteString(w, `{"status":"degraded"}`)
	})

	if err := client.HealthCheck(context.Background()); err == nil {
		t.Fatal("a degraded quant service must not report healthy to the API")
	}
}

func TestHealthCheckPassesWhenTheServiceIsOK(t *testing.T) {
	client := testClient(t, func(w http.ResponseWriter, _ *http.Request) {
		_, _ = io.WriteString(w, `{"status":"ok"}`)
	})

	if err := client.HealthCheck(context.Background()); err != nil {
		t.Fatalf("HealthCheck: %v", err)
	}
}

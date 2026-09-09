// Package quant is the Go API's client for the Python quant-mcp service.
//
// The transport is HTTP/JSON over the Compose network (planning.md §4
// decision 1). This package owns everything about that boundary — request
// shapes, timeouts, request-ID propagation, and translating quant-mcp's error
// envelope into typed Go errors — so no handler has to know the quant service
// exists as an HTTP endpoint.
package quant

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"log/slog"
	"net/http"
	"net/url"
	"strings"
	"time"

	"github.com/anubhavjha/ai-quant-terminal/apps/api/internal/logging"
)

const requestIDHeader = "X-Request-ID"

// UpstreamError is a structured failure reported by quant-mcp.
//
// The code and message are forwarded to the caller rather than collapsed into
// "upstream error": quant-mcp already produced a specific, user-facing
// explanation ("Unknown symbol 'NOPE'. Known symbols: …"), and replacing it
// with a generic message would throw away the only thing that tells the user
// what to do next.
type UpstreamError struct {
	StatusCode int
	Code       string
	Message    string
	Details    any
	RequestID  string
}

func (e *UpstreamError) Error() string {
	return fmt.Sprintf("quant-mcp %d %s: %s", e.StatusCode, e.Code, e.Message)
}

// ErrUnavailable means quant-mcp could not be reached at all — a different
// situation from quant-mcp rejecting the request, and one the user fixes by
// checking the stack rather than by changing their input.
var ErrUnavailable = errors.New("quant service is unreachable")

type errorEnvelope struct {
	Error struct {
		Code      string `json:"code"`
		Message   string `json:"message"`
		Details   any    `json:"details"`
		RequestID string `json:"request_id"`
	} `json:"error"`
}

// Client talks to quant-mcp.
type Client struct {
	baseURL string
	http    *http.Client
	log     *slog.Logger
}

// New builds a client. The timeout has to accommodate a multi-year backtest
// including a yfinance fetch, so it is deliberately generous.
func New(baseURL string, timeout time.Duration, log *slog.Logger) *Client {
	return &Client{
		baseURL: strings.TrimRight(baseURL, "/"),
		http:    &http.Client{Timeout: timeout},
		log:     log,
	}
}

// HealthCheck probes quant-mcp for the API's own /healthz registry.
func (c *Client) HealthCheck(ctx context.Context) error {
	req, err := http.NewRequestWithContext(ctx, http.MethodGet, c.baseURL+"/healthz", nil)
	if err != nil {
		return err
	}
	resp, err := c.http.Do(req)
	if err != nil {
		return fmt.Errorf("%w: %v", ErrUnavailable, err)
	}
	defer resp.Body.Close()
	if resp.StatusCode != http.StatusOK {
		return fmt.Errorf("quant-mcp reports unhealthy (status %d)", resp.StatusCode)
	}
	return nil
}

// BacktestRequest mirrors quant-mcp's POST /v1/backtests body.
type BacktestRequest struct {
	Symbol         string         `json:"symbol"`
	Strategy       string         `json:"strategy"`
	Start          string         `json:"start"`
	End            string         `json:"end"`
	Interval       string         `json:"interval,omitempty"`
	Parameters     map[string]any `json:"parameters,omitempty"`
	InitialCash    float64        `json:"initial_cash,omitempty"`
	CostModel      *CostModel     `json:"cost_model,omitempty"`
	ExecutionModel string         `json:"execution_model,omitempty"`
	AllowShort     bool           `json:"allow_short"`
	LiquidateAtEnd bool           `json:"liquidate_at_end"`
	RiskFreeRate   float64        `json:"risk_free_rate,omitempty"`
	ExperimentID   string         `json:"experiment_id,omitempty"`
}

// CostModel is the transaction-cost assumption sent with a backtest.
type CostModel struct {
	CommissionBps float64 `json:"commission_bps"`
	CommissionMin float64 `json:"commission_min"`
	SlippageBps   float64 `json:"slippage_bps"`
	SpreadBps     float64 `json:"spread_bps"`
}

// RunBacktest executes a backtest and returns the result contract verbatim.
//
// The payload is returned as raw JSON rather than being decoded into a Go
// struct mirroring every field. The contract is defined once, in Python
// (architecture.md §8); a hand-maintained Go copy would be a second definition
// free to drift from the first, and the API's job here is to carry the result,
// not to re-describe it. The fields the API genuinely reasons about are
// projected out separately by the backtest service.
func (c *Client) RunBacktest(ctx context.Context, req BacktestRequest) (json.RawMessage, error) {
	return c.postJSON(ctx, "/v1/backtests", req)
}

// MarketData fetches validated OHLCV bars with provenance and quality report.
func (c *Client) MarketData(ctx context.Context, symbol, start, end, interval string) (json.RawMessage, error) {
	query := url.Values{}
	query.Set("start", start)
	query.Set("end", end)
	if interval != "" {
		query.Set("interval", interval)
	}
	return c.get(ctx, "/v1/market/"+url.PathEscape(symbol)+"?"+query.Encode())
}

// Universe returns the tradable instruments.
func (c *Client) Universe(ctx context.Context) (json.RawMessage, error) {
	return c.get(ctx, "/v1/universe")
}

// Strategies returns every registered strategy with its parameter specs.
func (c *Client) Strategies(ctx context.Context) (json.RawMessage, error) {
	return c.get(ctx, "/v1/strategies")
}

func (c *Client) get(ctx context.Context, path string) (json.RawMessage, error) {
	req, err := http.NewRequestWithContext(ctx, http.MethodGet, c.baseURL+path, nil)
	if err != nil {
		return nil, err
	}
	return c.do(ctx, req, path)
}

func (c *Client) postJSON(ctx context.Context, path string, body any) (json.RawMessage, error) {
	encoded, err := json.Marshal(body)
	if err != nil {
		return nil, fmt.Errorf("encode request for %s: %w", path, err)
	}
	req, err := http.NewRequestWithContext(ctx, http.MethodPost, c.baseURL+path, bytes.NewReader(encoded))
	if err != nil {
		return nil, err
	}
	req.Header.Set("Content-Type", "application/json")
	return c.do(ctx, req, path)
}

func (c *Client) do(ctx context.Context, req *http.Request, path string) (json.RawMessage, error) {
	// Propagate the request ID so one user action is traceable across
	// web -> api -> quant-mcp in the logs (NFR2).
	if id := logging.RequestID(ctx); id != "" {
		req.Header.Set(requestIDHeader, id)
	}

	started := time.Now()
	resp, err := c.http.Do(req)
	if err != nil {
		return nil, fmt.Errorf("%w: %s: %v", ErrUnavailable, path, err)
	}
	defer resp.Body.Close()

	body, readErr := io.ReadAll(resp.Body)
	if c.log != nil {
		logging.FromContext(ctx, c.log).Info("quant-mcp call",
			"path", path, "status", resp.StatusCode,
			"duration_ms", time.Since(started).Milliseconds())
	}
	if readErr != nil {
		return nil, fmt.Errorf("read quant-mcp response for %s: %w", path, readErr)
	}

	if resp.StatusCode >= 400 {
		return nil, decodeUpstreamError(resp.StatusCode, body)
	}
	return json.RawMessage(body), nil
}

func decodeUpstreamError(status int, body []byte) error {
	var envelope errorEnvelope
	if err := json.Unmarshal(body, &envelope); err != nil || envelope.Error.Message == "" {
		// quant-mcp normally returns the shared envelope. If it did not, the
		// body is still the most informative thing available, so it is
		// forwarded (truncated) rather than discarded.
		snippet := string(body)
		if len(snippet) > 500 {
			snippet = snippet[:500] + "…"
		}
		return &UpstreamError{
			StatusCode: status,
			Code:       "upstream_failure",
			Message:    "The quant service returned an unexpected response: " + snippet,
		}
	}
	return &UpstreamError{
		StatusCode: status,
		Code:       envelope.Error.Code,
		Message:    envelope.Error.Message,
		Details:    envelope.Error.Details,
		RequestID:  envelope.Error.RequestID,
	}
}

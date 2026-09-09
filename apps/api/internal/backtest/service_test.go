package backtest

import (
	"context"
	"encoding/json"
	"errors"
	"strings"
	"testing"

	"github.com/anubhavjha/ai-quant-terminal/apps/api/internal/quant"
)

// stubRunner stands in for quant-mcp so the service can be tested without an
// HTTP server. The real HTTP boundary is covered by the integration test in
// tests/integration, which runs against the actual running service.
type stubRunner struct {
	payload json.RawMessage
	err     error
	last    quant.BacktestRequest
	calls   int
}

func (s *stubRunner) RunBacktest(_ context.Context, req quant.BacktestRequest) (json.RawMessage, error) {
	s.calls++
	s.last = req
	if s.err != nil {
		return nil, s.err
	}
	return s.payload, nil
}

const sampleResult = `{
  "strategy": "macd",
  "symbol": "RELIANCE.NS",
  "start": "2023-01-01",
  "end": "2023-12-31",
  "data_version": "sha256:abc123",
  "metrics": {"total_return": 0.12, "cagr": 0.11, "sharpe": 1.18, "max_drawdown": -0.11, "trade_count": 7}
}`

func validRequest() Request {
	return Request{
		Symbol:   "RELIANCE.NS",
		Strategy: "macd",
		Start:    "2023-01-01",
		End:      "2023-12-31",
	}
}

func newTestService(runner Runner) *Service {
	return NewService(runner, NewMemoryStore(10))
}

func TestRunStoresAndProjectsTheResult(t *testing.T) {
	runner := &stubRunner{payload: json.RawMessage(sampleResult)}
	service := newTestService(runner)

	record, err := service.Run(context.Background(), validRequest())
	if err != nil {
		t.Fatalf("Run: %v", err)
	}

	if !strings.HasPrefix(record.ID, "bt_") {
		t.Errorf("ID = %q, want a bt_ prefixed identifier", record.ID)
	}
	if record.DataVersion != "sha256:abc123" {
		t.Errorf("DataVersion = %q, want it projected from the result", record.DataVersion)
	}
	if record.MetricsBrief.TradeCount != 7 {
		t.Errorf("TradeCount = %d, want 7", record.MetricsBrief.TradeCount)
	}
	if record.MetricsBrief.Sharpe == nil || *record.MetricsBrief.Sharpe != 1.18 {
		t.Errorf("Sharpe = %v, want 1.18", record.MetricsBrief.Sharpe)
	}
	// The engine's contract must survive intact, not be re-serialized from the
	// projection.
	var payload map[string]any
	if err := json.Unmarshal(record.Result, &payload); err != nil {
		t.Fatalf("stored result is not the engine payload: %v", err)
	}
	if payload["strategy"] != "macd" {
		t.Errorf("stored result lost fields: %v", payload)
	}
}

func TestRunAppliesDefaultsAndPassesTheExperimentID(t *testing.T) {
	runner := &stubRunner{payload: json.RawMessage(sampleResult)}
	service := newTestService(runner)

	record, err := service.Run(context.Background(), validRequest())
	if err != nil {
		t.Fatalf("Run: %v", err)
	}

	if runner.last.Interval != "1d" {
		t.Errorf("Interval = %q, want the 1d default", runner.last.Interval)
	}
	if runner.last.InitialCash != 1_000_000 {
		t.Errorf("InitialCash = %v, want the 1,000,000 default", runner.last.InitialCash)
	}
	if runner.last.ExecutionModel != "next_bar_open" {
		t.Errorf("ExecutionModel = %q, want the look-ahead-safe default", runner.last.ExecutionModel)
	}
	// Leaving a position open at the end would book an unrealised gain as
	// though it had been cashed out for free.
	if !runner.last.LiquidateAtEnd {
		t.Error("LiquidateAtEnd should default to true")
	}
	// The ID is generated before the run so the engine can stamp it into the
	// result contract, making a stored result self-identifying.
	if runner.last.ExperimentID != record.ID {
		t.Errorf("ExperimentID = %q, want it to match the record ID %q", runner.last.ExperimentID, record.ID)
	}
}

func TestLiquidateAtEndCanBeTurnedOffExplicitly(t *testing.T) {
	runner := &stubRunner{payload: json.RawMessage(sampleResult)}
	service := newTestService(runner)

	off := false
	req := validRequest()
	req.LiquidateAtEnd = &off

	if _, err := service.Run(context.Background(), req); err != nil {
		t.Fatalf("Run: %v", err)
	}
	if runner.last.LiquidateAtEnd {
		t.Error("an explicit false must be honoured, not overridden by the default")
	}
}

func TestValidationRejectsBadRequestsBeforeCallingTheEngine(t *testing.T) {
	tests := []struct {
		name    string
		mutate  func(*Request)
		wantMsg string
	}{
		{"missing symbol", func(r *Request) { r.Symbol = "" }, "symbol is required"},
		{"missing strategy", func(r *Request) { r.Strategy = "" }, "strategy is required"},
		{"bad start", func(r *Request) { r.Start = "01/01/2023" }, "start must be an ISO date"},
		{"bad end", func(r *Request) { r.End = "yesterday" }, "end must be an ISO date"},
		{"reversed range", func(r *Request) { r.Start, r.End = "2023-12-31", "2023-01-01" }, "start must not be after end"},
		{"negative cash", func(r *Request) { r.InitialCash = -5 }, "initial_cash must be positive"},
	}

	for _, tc := range tests {
		t.Run(tc.name, func(t *testing.T) {
			runner := &stubRunner{payload: json.RawMessage(sampleResult)}
			service := newTestService(runner)
			req := validRequest()
			tc.mutate(&req)

			_, err := service.Run(context.Background(), req)
			if !errors.Is(err, ErrInvalidRequest) {
				t.Fatalf("error = %v, want ErrInvalidRequest", err)
			}
			if !strings.Contains(err.Error(), tc.wantMsg) {
				t.Errorf("error %q should mention %q", err, tc.wantMsg)
			}
			if runner.calls != 0 {
				t.Error("an invalid request must not reach the quant engine")
			}
		})
	}
}

func TestUnknownSymbolIsLeftToTheEngine(t *testing.T) {
	// The engine owns the universe. A second list in Go would drift and start
	// rejecting symbols that actually work.
	runner := &stubRunner{payload: json.RawMessage(sampleResult)}
	service := newTestService(runner)

	req := validRequest()
	req.Symbol = "SOMETHING.NEW"

	if _, err := service.Run(context.Background(), req); err != nil {
		t.Fatalf("Run: %v", err)
	}
	if runner.calls != 1 {
		t.Error("the request should have been forwarded for the engine to judge")
	}
}

func TestUpstreamErrorIsPropagatedUnchanged(t *testing.T) {
	upstream := &quant.UpstreamError{
		StatusCode: 400,
		Code:       "invalid_request",
		Message:    "Strategy 'macd' needs at least 35 bars. Widen the date range.",
	}
	service := newTestService(&stubRunner{err: upstream})

	_, err := service.Run(context.Background(), validRequest())

	var got *quant.UpstreamError
	if !errors.As(err, &got) {
		t.Fatalf("error = %v, want the upstream error preserved", err)
	}
	// The actionable message is the only part the user can do anything with.
	if !strings.Contains(got.Message, "Widen the date range") {
		t.Errorf("message = %q, want the engine's guidance preserved", got.Message)
	}
}

func TestGetReturnsNotFoundForAnUnknownID(t *testing.T) {
	service := newTestService(&stubRunner{payload: json.RawMessage(sampleResult)})

	_, err := service.Get(context.Background(), "bt_does_not_exist")

	if !errors.Is(err, ErrNotFound) {
		t.Fatalf("error = %v, want ErrNotFound", err)
	}
}

func TestRunThenGetRoundTrip(t *testing.T) {
	service := newTestService(&stubRunner{payload: json.RawMessage(sampleResult)})

	created, err := service.Run(context.Background(), validRequest())
	if err != nil {
		t.Fatalf("Run: %v", err)
	}
	fetched, err := service.Get(context.Background(), created.ID)
	if err != nil {
		t.Fatalf("Get: %v", err)
	}

	if string(fetched.Result) != string(created.Result) {
		t.Error("the stored result must be byte-identical to what was returned")
	}
}

func TestListReturnsNewestFirstAndRespectsTheLimit(t *testing.T) {
	service := newTestService(&stubRunner{payload: json.RawMessage(sampleResult)})

	var ids []string
	for i := 0; i < 5; i++ {
		record, err := service.Run(context.Background(), validRequest())
		if err != nil {
			t.Fatalf("Run: %v", err)
		}
		ids = append(ids, record.ID)
	}

	records, err := service.List(context.Background(), 3)
	if err != nil {
		t.Fatalf("List: %v", err)
	}
	if len(records) != 3 {
		t.Fatalf("got %d records, want 3", len(records))
	}
	if records[0].ID != ids[len(ids)-1] {
		t.Errorf("first record = %q, want the most recent %q", records[0].ID, ids[len(ids)-1])
	}
}

func TestMemoryStoreEvictsOldestBeyondCapacity(t *testing.T) {
	// Bounded so a long-running local process cannot grow without limit. An
	// evicted ID must report not-found rather than a stale or partial result.
	store := NewMemoryStore(2)
	service := NewService(&stubRunner{payload: json.RawMessage(sampleResult)}, store)

	first, _ := service.Run(context.Background(), validRequest())
	_, _ = service.Run(context.Background(), validRequest())
	_, _ = service.Run(context.Background(), validRequest())

	if _, err := service.Get(context.Background(), first.ID); !errors.Is(err, ErrNotFound) {
		t.Fatalf("error = %v, want the oldest record to have been evicted", err)
	}
}

func TestProjectionToleratesAResultItCannotParse(t *testing.T) {
	// Refusing to return a completed backtest because a list-view number could
	// not be extracted would be the wrong trade — the authoritative result is
	// still intact.
	service := newTestService(&stubRunner{payload: json.RawMessage(`"not an object"`)})

	record, err := service.Run(context.Background(), validRequest())
	if err != nil {
		t.Fatalf("Run: %v", err)
	}
	if record.Symbol != "RELIANCE.NS" {
		t.Errorf("Symbol = %q, want the request's value retained", record.Symbol)
	}
	if record.MetricsBrief.TradeCount != 0 {
		t.Error("an unparseable result should leave the projection empty, not invent values")
	}
}

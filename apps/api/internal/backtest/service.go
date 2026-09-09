// Package backtest is the application service between the HTTP layer and the
// quant engine.
//
// Handlers contain no business logic and no quantitative formulas
// (architecture.md §3.2/§13): they decode a request, call this service, and
// render the result.
package backtest

import (
	"context"
	"crypto/rand"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"strings"
	"time"

	"github.com/anubhavjha/ai-quant-terminal/apps/api/internal/quant"
)

// ErrNotFound means no backtest with that ID is known to this service.
var ErrNotFound = errors.New("backtest not found")

// ErrInvalidRequest means the request was rejected before reaching the engine.
var ErrInvalidRequest = errors.New("invalid backtest request")

// Runner is the capability this service needs from the quant engine. Declaring
// it here rather than depending on *quant.Client keeps the service testable
// without an HTTP server, and keeps the dependency pointing inwards.
type Runner interface {
	RunBacktest(ctx context.Context, req quant.BacktestRequest) (json.RawMessage, error)
}

// Store persists completed backtests. The in-memory implementation is the M2
// walking skeleton; PostgreSQL-backed experiment persistence arrives at M3.5.
type Store interface {
	Save(ctx context.Context, record Record) error
	Get(ctx context.Context, id string) (Record, error)
	List(ctx context.Context, limit int) ([]Record, error)
}

// Record is one completed backtest as the API stores and returns it.
//
// The engine's result contract is kept verbatim in Result. The projected fields
// beside it exist so the API can list and filter backtests without parsing the
// whole payload — they are a *view* of the contract, never a second definition
// of it.
type Record struct {
	ID           string          `json:"id"`
	Symbol       string          `json:"symbol"`
	Strategy     string          `json:"strategy"`
	Start        string          `json:"start"`
	End          string          `json:"end"`
	DataVersion  string          `json:"data_version"`
	CreatedAt    time.Time       `json:"created_at"`
	Result       json.RawMessage `json:"result"`
	MetricsBrief MetricsBrief    `json:"metrics_brief"`
}

// MetricsBrief is the handful of numbers a list view shows.
type MetricsBrief struct {
	TotalReturn *float64 `json:"total_return"`
	CAGR        *float64 `json:"cagr"`
	Sharpe      *float64 `json:"sharpe"`
	MaxDrawdown *float64 `json:"max_drawdown"`
	TradeCount  int      `json:"trade_count"`
}

// Request is the API-level backtest request, before defaults are applied.
type Request struct {
	Symbol         string           `json:"symbol"`
	Strategy       string           `json:"strategy"`
	Start          string           `json:"start"`
	End            string           `json:"end"`
	Interval       string           `json:"interval"`
	Parameters     map[string]any   `json:"parameters"`
	InitialCash    float64          `json:"initial_cash"`
	CostModel      *quant.CostModel `json:"cost_model"`
	ExecutionModel string           `json:"execution_model"`
	AllowShort     bool             `json:"allow_short"`
	LiquidateAtEnd *bool            `json:"liquidate_at_end"`
	RiskFreeRate   float64          `json:"risk_free_rate"`
}

// Validate checks what the API can check without calling the engine.
//
// Symbol and strategy names are deliberately *not* validated here: the engine
// owns the universe and the strategy registry, and a second list in Go would be
// free to drift out of date and reject something that actually works.
func (r Request) Validate() error {
	var problems []string
	if strings.TrimSpace(r.Symbol) == "" {
		problems = append(problems, "symbol is required")
	}
	if strings.TrimSpace(r.Strategy) == "" {
		problems = append(problems, "strategy is required")
	}
	if !isISODate(r.Start) {
		problems = append(problems, "start must be an ISO date (YYYY-MM-DD)")
	}
	if !isISODate(r.End) {
		problems = append(problems, "end must be an ISO date (YYYY-MM-DD)")
	}
	if isISODate(r.Start) && isISODate(r.End) && r.Start > r.End {
		problems = append(problems, "start must not be after end")
	}
	if r.InitialCash < 0 {
		problems = append(problems, "initial_cash must be positive")
	}
	if len(problems) > 0 {
		return fmt.Errorf("%w: %s", ErrInvalidRequest, strings.Join(problems, "; "))
	}
	return nil
}

func isISODate(value string) bool {
	_, err := time.Parse(time.DateOnly, value)
	return err == nil
}

// Service runs backtests and remembers the results.
type Service struct {
	runner Runner
	store  Store
}

func NewService(runner Runner, store Store) *Service {
	return &Service{runner: runner, store: store}
}

// Run executes a backtest and stores the result under a generated ID.
func (s *Service) Run(ctx context.Context, req Request) (Record, error) {
	if err := req.Validate(); err != nil {
		return Record{}, err
	}

	id := newID()
	payload, err := s.runner.RunBacktest(ctx, quant.BacktestRequest{
		Symbol:         req.Symbol,
		Strategy:       req.Strategy,
		Start:          req.Start,
		End:            req.End,
		Interval:       defaultString(req.Interval, "1d"),
		Parameters:     req.Parameters,
		InitialCash:    defaultFloat(req.InitialCash, 1_000_000),
		CostModel:      req.CostModel,
		ExecutionModel: defaultString(req.ExecutionModel, "next_bar_open"),
		AllowShort:     req.AllowShort,
		// Defaults to true: leaving a position open at the end would book an
		// unrealised gain as though it had been cashed out for free.
		LiquidateAtEnd: req.LiquidateAtEnd == nil || *req.LiquidateAtEnd,
		RiskFreeRate:   req.RiskFreeRate,
		ExperimentID:   id,
	})
	if err != nil {
		return Record{}, err
	}

	record := Record{
		ID:        id,
		Symbol:    req.Symbol,
		Strategy:  req.Strategy,
		Start:     req.Start,
		End:       req.End,
		CreatedAt: time.Now().UTC(),
		Result:    payload,
	}
	projectFields(&record)

	if err := s.store.Save(ctx, record); err != nil {
		return Record{}, fmt.Errorf("store backtest %s: %w", id, err)
	}
	return record, nil
}

// Get returns a previously run backtest.
func (s *Service) Get(ctx context.Context, id string) (Record, error) {
	return s.store.Get(ctx, id)
}

// List returns recent backtests, newest first.
func (s *Service) List(ctx context.Context, limit int) ([]Record, error) {
	if limit <= 0 || limit > 200 {
		limit = 50
	}
	return s.store.List(ctx, limit)
}

// projectFields reads the few fields the API reasons about out of the engine's
// result contract. A missing field leaves the projection empty rather than
// failing the whole request: the authoritative result is still intact, and
// refusing to return a completed backtest because a list-view number could not
// be extracted would be the wrong trade.
func projectFields(record *Record) {
	var view struct {
		Symbol      string `json:"symbol"`
		Strategy    string `json:"strategy"`
		Start       string `json:"start"`
		End         string `json:"end"`
		DataVersion string `json:"data_version"`
		Metrics     struct {
			TotalReturn *float64 `json:"total_return"`
			CAGR        *float64 `json:"cagr"`
			Sharpe      *float64 `json:"sharpe"`
			MaxDrawdown *float64 `json:"max_drawdown"`
			TradeCount  int      `json:"trade_count"`
		} `json:"metrics"`
	}
	if err := json.Unmarshal(record.Result, &view); err != nil {
		return
	}
	if view.Symbol != "" {
		record.Symbol = view.Symbol
	}
	if view.Strategy != "" {
		record.Strategy = view.Strategy
	}
	if view.Start != "" {
		record.Start = view.Start
	}
	if view.End != "" {
		record.End = view.End
	}
	record.DataVersion = view.DataVersion
	record.MetricsBrief = MetricsBrief{
		TotalReturn: view.Metrics.TotalReturn,
		CAGR:        view.Metrics.CAGR,
		Sharpe:      view.Metrics.Sharpe,
		MaxDrawdown: view.Metrics.MaxDrawdown,
		TradeCount:  view.Metrics.TradeCount,
	}
}

func newID() string {
	var b [10]byte
	if _, err := rand.Read(b[:]); err != nil {
		return "bt_" + time.Now().UTC().Format("20060102150405.000000000")
	}
	return "bt_" + hex.EncodeToString(b[:])
}

func defaultString(value, fallback string) string {
	if strings.TrimSpace(value) == "" {
		return fallback
	}
	return value
}

func defaultFloat(value, fallback float64) float64 {
	if value <= 0 {
		return fallback
	}
	return value
}

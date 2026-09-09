// Package experiment owns the reproducible-unit lifecycle: save a backtest with
// everything needed to rerun it, and rerun it later without the user having to
// remember any configuration (requirements.md FR5, NFR6).
//
// The domain model here is not coupled to the SQL schema (architecture.md §15).
// The repository interface below is what the service depends on; the PostgreSQL
// implementation lives in internal/storage/postgres and is free to change its
// column layout without this package noticing.
package experiment

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"strings"
	"time"

	"github.com/anubhavjha/ai-quant-terminal/apps/api/internal/quant"
)

// ErrNotFound means no experiment with that ID exists.
var ErrNotFound = errors.New("experiment not found")

// ErrInvalid means the save request was rejected before touching the database.
var ErrInvalid = errors.New("invalid experiment")

// Experiment is a saved, rerunnable backtest.
//
// Every field the engine needs to reproduce the run is stored explicitly rather
// than being re-derived: a rerun must not depend on today's defaults matching
// the defaults in force when the experiment was saved.
type Experiment struct {
	ID       string `json:"id"`
	Name     string `json:"name,omitempty"`
	Notes    string `json:"notes,omitempty"`
	Strategy string `json:"strategy"`
	Symbol   string `json:"symbol"`
	Universe string `json:"universe,omitempty"`
	Interval string `json:"interval"`
	Start    string `json:"start"`
	End      string `json:"end"`

	Parameters     map[string]any `json:"parameters"`
	CostModel      map[string]any `json:"cost_model"`
	BacktestConfig map[string]any `json:"backtest_config"`

	// DataVersion is the content hash of the bars the original run used. A
	// rerun compares against it to distinguish "the code changed" from "the
	// source data changed underneath us".
	DataVersion     string `json:"data_version"`
	CodeVersion     string `json:"code_version"`
	ContractVersion int    `json:"contract_version"`

	Result    json.RawMessage `json:"result"`
	CreatedAt time.Time       `json:"created_at"`
}

// Summary is the list-view projection, without the bulky result payload.
type Summary struct {
	ID          string    `json:"id"`
	Name        string    `json:"name,omitempty"`
	Strategy    string    `json:"strategy"`
	Symbol      string    `json:"symbol"`
	Interval    string    `json:"interval"`
	Start       string    `json:"start"`
	End         string    `json:"end"`
	DataVersion string    `json:"data_version"`
	CreatedAt   time.Time `json:"created_at"`
	Metrics     Metrics   `json:"metrics"`
}

// Metrics is the handful of numbers the journal shows per row.
type Metrics struct {
	TotalReturn *float64 `json:"total_return"`
	CAGR        *float64 `json:"cagr"`
	Sharpe      *float64 `json:"sharpe"`
	Sortino     *float64 `json:"sortino"`
	MaxDrawdown *float64 `json:"max_drawdown"`
	WinRate     *float64 `json:"win_rate"`
	TradeCount  int      `json:"trade_count"`
}

// Repository is the persistence boundary. Domain types in, domain types out —
// no rows, no SQL, no driver types cross it.
type Repository interface {
	Save(ctx context.Context, exp Experiment) error
	Get(ctx context.Context, id string) (Experiment, error)
	List(ctx context.Context, filter ListFilter) ([]Summary, error)
	Delete(ctx context.Context, id string) error
}

// ListFilter narrows the journal view.
type ListFilter struct {
	Strategy string
	Symbol   string
	Limit    int
}

// SaveRequest is what a caller supplies to persist a run.
//
// Exactly one of BacktestID (promote a backtest already run this session) or
// the inline run configuration is used.
type SaveRequest struct {
	Name       string `json:"name"`
	Notes      string `json:"notes"`
	BacktestID string `json:"backtest_id"`

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

// Validate checks the request shape.
func (r SaveRequest) Validate() error {
	if strings.TrimSpace(r.BacktestID) != "" {
		return nil
	}
	var problems []string
	if strings.TrimSpace(r.Symbol) == "" {
		problems = append(problems, "symbol is required (or supply backtest_id)")
	}
	if strings.TrimSpace(r.Strategy) == "" {
		problems = append(problems, "strategy is required (or supply backtest_id)")
	}
	if !isISODate(r.Start) || !isISODate(r.End) {
		problems = append(problems, "start and end must be ISO dates (YYYY-MM-DD)")
	}
	if len(problems) > 0 {
		return fmt.Errorf("%w: %s", ErrInvalid, strings.Join(problems, "; "))
	}
	return nil
}

func isISODate(value string) bool {
	_, err := time.Parse(time.DateOnly, value)
	return err == nil
}

// RerunOutcome is the verdict of rerunning a saved experiment.
//
// It reports three things separately because they have different meanings and
// different fixes: whether the underlying data is still the same
// (DataVersionMatches), whether the numbers came out the same (MetricsMatch),
// and, when they did not, exactly which metrics moved. Collapsing these into a
// single boolean would leave a user unable to tell a code regression from a
// provider revising history (NFR6).
type RerunOutcome struct {
	ExperimentID        string          `json:"experiment_id"`
	Reproducible        bool            `json:"reproducible"`
	DataVersionMatches  bool            `json:"data_version_matches"`
	MetricsMatch        bool            `json:"metrics_match"`
	OriginalDataVersion string          `json:"original_data_version"`
	RerunDataVersion    string          `json:"rerun_data_version"`
	Differences         []MetricDiff    `json:"differences"`
	Explanation         string          `json:"explanation"`
	OriginalResult      json.RawMessage `json:"original_result"`
	RerunResult         json.RawMessage `json:"rerun_result"`
}

// MetricDiff is one metric that changed between the original run and the rerun.
type MetricDiff struct {
	Metric   string   `json:"metric"`
	Original *float64 `json:"original"`
	Rerun    *float64 `json:"rerun"`
}

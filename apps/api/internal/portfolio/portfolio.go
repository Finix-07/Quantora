// Package portfolio owns the holdings a user maintains and the risk reports
// derived from them (requirements.md FR7).
//
// It contains no portfolio mathematics. Allocation, concentration, beta,
// correlation, drawdown and the scenario comparison are all computed by the
// Python engine and reached over HTTP (architecture.md §3.2, §13); a Go copy of
// any of those formulas would be a second definition free to disagree with the
// first, and the disagreement would surface as two different answers to the same
// question depending on which surface the user asked through.
//
// The domain model here is not coupled to the SQL schema (architecture.md §15).
// The repository interface below is what the service depends on; the PostgreSQL
// implementation lives in internal/storage/postgres.
package portfolio

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"math"
	"strings"
	"time"
)

// ErrNotFound means no portfolio or report with that ID exists.
var ErrNotFound = errors.New("portfolio not found")

// ErrInvalid means the request was rejected before reaching the database or the
// engine.
var ErrInvalid = errors.New("invalid portfolio request")

// Holding is one position: how much of what, and optionally what it cost.
type Holding struct {
	Symbol   string  `json:"symbol"`
	Quantity float64 `json:"quantity"`
	// CostBasis is a pointer so an unrecorded cost stays distinguishable from a
	// cost of zero. The engine reports no unrealised P&L for the first and a
	// 100% gain for the second.
	CostBasis *float64 `json:"cost_basis,omitempty"`
}

// Portfolio is a named, mutable set of holdings.
type Portfolio struct {
	ID           string    `json:"id"`
	Name         string    `json:"name"`
	Description  string    `json:"description,omitempty"`
	BaseCurrency string    `json:"base_currency"`
	Benchmark    string    `json:"benchmark"`
	Holdings     []Holding `json:"holdings"`
	CreatedAt    time.Time `json:"created_at"`
	UpdatedAt    time.Time `json:"updated_at"`
}

// Summary is the list-view projection, without the holdings.
type Summary struct {
	ID           string    `json:"id"`
	Name         string    `json:"name"`
	Description  string    `json:"description,omitempty"`
	BaseCurrency string    `json:"base_currency"`
	Benchmark    string    `json:"benchmark"`
	HoldingCount int       `json:"holding_count"`
	CreatedAt    time.Time `json:"created_at"`
	UpdatedAt    time.Time `json:"updated_at"`
}

// Report kinds. A scenario report holds a before/after comparison; a risk
// report holds a single state. Recorded explicitly so a reader never has to
// infer which is inside from the payload's shape.
const (
	KindRisk     = "risk"
	KindScenario = "scenario"
)

// Report is a saved analysis, frozen at the moment it was run.
type Report struct {
	ID          string  `json:"id"`
	PortfolioID string  `json:"portfolio_id,omitempty"`
	Name        string  `json:"name,omitempty"`
	Kind        string  `json:"kind"`
	Benchmark   string  `json:"benchmark"`
	Interval    string  `json:"interval"`
	Start       string  `json:"start"`
	End         string  `json:"end"`
	AsOf        string  `json:"as_of,omitempty"`
	Metrics     Metrics `json:"metrics"`

	// Payload is the engine's report verbatim.
	Payload   json.RawMessage `json:"report"`
	CreatedAt time.Time       `json:"created_at"`
}

// ReportSummary is the report list's row, without the payload.
type ReportSummary struct {
	ID          string    `json:"id"`
	PortfolioID string    `json:"portfolio_id,omitempty"`
	Name        string    `json:"name,omitempty"`
	Kind        string    `json:"kind"`
	Benchmark   string    `json:"benchmark"`
	Interval    string    `json:"interval"`
	Start       string    `json:"start"`
	End         string    `json:"end"`
	AsOf        string    `json:"as_of,omitempty"`
	Metrics     Metrics   `json:"metrics"`
	CreatedAt   time.Time `json:"created_at"`
}

// Metrics is the handful of numbers the report list shows per row.
//
// Every field is a pointer. A nil is the engine saying "this metric is not
// available", with the reason recorded inside the full report; it must never be
// rendered as 0. A beta of 0 claims the portfolio does not move with the market,
// which is a measurement — nil is the absence of one (NFR5.6).
type Metrics struct {
	TotalValue           *float64 `json:"total_value"`
	AnnualizedReturn     *float64 `json:"annualized_return"`
	AnnualizedVolatility *float64 `json:"annualized_volatility"`
	Beta                 *float64 `json:"beta"`
	Sharpe               *float64 `json:"sharpe"`
	MaxDrawdown          *float64 `json:"max_drawdown"`
}

// Repository is the persistence boundary. Domain types in, domain types out —
// no rows, no SQL and no driver types cross it.
type Repository interface {
	SavePortfolio(ctx context.Context, p Portfolio) error
	GetPortfolio(ctx context.Context, id string) (Portfolio, error)
	ListPortfolios(ctx context.Context, limit int) ([]Summary, error)
	DeletePortfolio(ctx context.Context, id string) error

	SaveReport(ctx context.Context, report Report) error
	GetReport(ctx context.Context, id string) (Report, error)
	ListReports(ctx context.Context, filter ReportFilter) ([]ReportSummary, error)
}

// ReportFilter narrows the saved-report list.
type ReportFilter struct {
	PortfolioID string
	Kind        string
	Limit       int
}

// SaveRequest creates a portfolio.
type SaveRequest struct {
	Name         string    `json:"name"`
	Description  string    `json:"description"`
	BaseCurrency string    `json:"base_currency"`
	Benchmark    string    `json:"benchmark"`
	Holdings     []Holding `json:"holdings"`
}

// Validate checks the request shape.
//
// Only the rules the API can check without market data live here. Whether a
// symbol exists, whether the universe knows its sector and whether the holdings
// have enough overlapping history are the engine's to answer, and duplicating
// its judgement in Go would give the same request two different explanations
// depending on which layer noticed first.
func (r SaveRequest) Validate() error {
	var problems []string
	if strings.TrimSpace(r.Name) == "" {
		problems = append(problems, "name is required")
	}
	if len(r.Holdings) == 0 {
		problems = append(problems, "at least one holding is required")
	}
	seen := map[string]bool{}
	for i, h := range r.Holdings {
		symbol := strings.TrimSpace(h.Symbol)
		if symbol == "" {
			problems = append(problems, fmt.Sprintf("holdings[%d] is missing a symbol", i))
			continue
		}
		if seen[symbol] {
			problems = append(problems, fmt.Sprintf(
				"%s appears twice; combine the rows into one with the total quantity", symbol))
		}
		seen[symbol] = true
		if !isPositiveFinite(h.Quantity) {
			problems = append(problems, fmt.Sprintf(
				"holdings[%d] (%s) needs a positive quantity; short positions are not modelled",
				i, symbol))
		}
		if h.CostBasis != nil && (math.IsNaN(*h.CostBasis) || *h.CostBasis < 0) {
			problems = append(problems, fmt.Sprintf(
				"holdings[%d] (%s) has a negative cost basis", i, symbol))
		}
	}
	if len(problems) > 0 {
		return fmt.Errorf("%w: %s", ErrInvalid, strings.Join(problems, "; "))
	}
	return nil
}

// AnalyzeRequest asks for risk metrics.
//
// Either PortfolioID (analyse a saved portfolio) or Holdings (analyse an
// unsaved set) is supplied. Both is a contradiction rather than a merge: there
// would be no way to tell which set the resulting report described.
type AnalyzeRequest struct {
	PortfolioID  string    `json:"portfolio_id"`
	Holdings     []Holding `json:"holdings"`
	Start        string    `json:"start"`
	End          string    `json:"end"`
	Interval     string    `json:"interval"`
	Benchmark    string    `json:"benchmark"`
	RiskFreeRate float64   `json:"risk_free_rate"`
	SaveAs       string    `json:"save_as"`
	// SaveReport persists the result. Off by default: an exploratory analysis
	// that silently filled the report list would make the saved ones harder to
	// find, and saving is the user's decision to record a moment in time.
	SaveReport bool `json:"save_report"`
}

// ScenarioRequest asks for a reweighting comparison.
type ScenarioRequest struct {
	AnalyzeRequest
	Weights map[string]float64 `json:"weights"`
}

func (r AnalyzeRequest) validate() []string {
	var problems []string
	hasID := strings.TrimSpace(r.PortfolioID) != ""
	if hasID && len(r.Holdings) > 0 {
		problems = append(problems,
			"supply either portfolio_id or holdings, not both — otherwise the saved report would "+
				"not say which set of holdings it described")
	}
	if !hasID && len(r.Holdings) == 0 {
		problems = append(problems, "portfolio_id or holdings is required")
	}
	if !isISODate(r.Start) || !isISODate(r.End) {
		problems = append(problems, "start and end must be ISO dates (YYYY-MM-DD)")
	} else if r.Start > r.End {
		problems = append(problems, "start must not be after end")
	}
	for i, h := range r.Holdings {
		if strings.TrimSpace(h.Symbol) == "" {
			problems = append(problems, fmt.Sprintf("holdings[%d] is missing a symbol", i))
		}
		if !isPositiveFinite(h.Quantity) {
			problems = append(problems, fmt.Sprintf(
				"holdings[%d] needs a positive quantity", i))
		}
	}
	return problems
}

// Validate checks an analysis request.
func (r AnalyzeRequest) Validate() error {
	if problems := r.validate(); len(problems) > 0 {
		return fmt.Errorf("%w: %s", ErrInvalid, strings.Join(problems, "; "))
	}
	return nil
}

// Validate checks a scenario request.
//
// It checks only that *some* reweighting was asked for. The weight vector's own
// rules — non-negative, finite, not all zero — belong to the engine, which
// explains each of them in terms the user can act on ("a negative weight is a
// short position, which the portfolio engine does not model"). Re-implementing
// them here would replace those explanations with a Go paraphrase that could
// drift from the behaviour it describes.
func (r ScenarioRequest) Validate() error {
	problems := r.AnalyzeRequest.validate()
	if len(r.Weights) == 0 {
		problems = append(problems,
			"weights is required: a scenario must change at least one weight")
	}
	if len(problems) > 0 {
		return fmt.Errorf("%w: %s", ErrInvalid, strings.Join(problems, "; "))
	}
	return nil
}

func isPositiveFinite(value float64) bool {
	return value > 0 && !math.IsInf(value, 0) && !math.IsNaN(value)
}

func isISODate(value string) bool {
	_, err := time.Parse(time.DateOnly, value)
	return err == nil
}

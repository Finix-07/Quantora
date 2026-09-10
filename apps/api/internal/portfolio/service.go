package portfolio

import (
	"context"
	"crypto/rand"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"strings"
	"time"

	"github.com/anubhavjha/ai-quant-terminal/apps/api/internal/quant"
)

const (
	defaultBenchmark = "NIFTY"
	defaultCurrency  = "INR"
	defaultInterval  = "1d"
)

// Analyzer is the capability this service needs from the quant engine.
// Declaring it here rather than depending on *quant.Client keeps the service
// testable without an HTTP server and keeps the dependency pointing inwards.
type Analyzer interface {
	AnalyzePortfolioRisk(ctx context.Context, req quant.PortfolioRiskRequest) (json.RawMessage, error)
	RunPortfolioScenario(ctx context.Context, req quant.PortfolioScenarioRequest) (json.RawMessage, error)
}

// Service owns the portfolio lifecycle and delegates every calculation.
//
// It contains no portfolio mathematics. Beta, volatility and concentration are
// computed once, in the Python engine, and a Go reimplementation would be a
// second definition free to disagree with the one the MCP layer and the UI see.
type Service struct {
	repo     Repository
	analyzer Analyzer
}

func NewService(repo Repository, analyzer Analyzer) *Service {
	return &Service{repo: repo, analyzer: analyzer}
}

// Save creates or replaces a portfolio.
func (s *Service) Save(ctx context.Context, req SaveRequest) (Portfolio, error) {
	if err := req.Validate(); err != nil {
		return Portfolio{}, err
	}

	now := time.Now().UTC()
	p := Portfolio{
		ID:           newID("pf"),
		Name:         strings.TrimSpace(req.Name),
		Description:  strings.TrimSpace(req.Description),
		BaseCurrency: orDefault(req.BaseCurrency, defaultCurrency),
		Benchmark:    orDefault(req.Benchmark, defaultBenchmark),
		Holdings:     req.Holdings,
		CreatedAt:    now,
		UpdatedAt:    now,
	}
	if err := s.repo.SavePortfolio(ctx, p); err != nil {
		return Portfolio{}, fmt.Errorf("persist portfolio: %w", err)
	}
	return p, nil
}

// Update replaces a saved portfolio's holdings and metadata, keeping its ID.
//
// The ID is preserved rather than a new portfolio being created, so saved
// reports keep pointing at the thing the user still thinks of as the same
// portfolio. Reports are snapshots and are deliberately not recomputed: a
// report describes the holdings as they were when it was run.
func (s *Service) Update(ctx context.Context, id string, req SaveRequest) (Portfolio, error) {
	if err := req.Validate(); err != nil {
		return Portfolio{}, err
	}
	existing, err := s.repo.GetPortfolio(ctx, id)
	if err != nil {
		return Portfolio{}, err
	}

	updated := Portfolio{
		ID:           existing.ID,
		Name:         strings.TrimSpace(req.Name),
		Description:  strings.TrimSpace(req.Description),
		BaseCurrency: orDefault(req.BaseCurrency, existing.BaseCurrency),
		Benchmark:    orDefault(req.Benchmark, existing.Benchmark),
		Holdings:     req.Holdings,
		CreatedAt:    existing.CreatedAt,
		UpdatedAt:    time.Now().UTC(),
	}
	if err := s.repo.SavePortfolio(ctx, updated); err != nil {
		return Portfolio{}, fmt.Errorf("persist portfolio: %w", err)
	}
	return updated, nil
}

func (s *Service) Get(ctx context.Context, id string) (Portfolio, error) {
	return s.repo.GetPortfolio(ctx, id)
}

func (s *Service) List(ctx context.Context, limit int) ([]Summary, error) {
	return s.repo.ListPortfolios(ctx, clampLimit(limit))
}

func (s *Service) Delete(ctx context.Context, id string) error {
	return s.repo.DeletePortfolio(ctx, id)
}

func (s *Service) GetReport(ctx context.Context, id string) (Report, error) {
	return s.repo.GetReport(ctx, id)
}

func (s *Service) ListReports(ctx context.Context, filter ReportFilter) ([]ReportSummary, error) {
	filter.Limit = clampLimit(filter.Limit)
	return s.repo.ListReports(ctx, filter)
}

// resolve turns a request into the holdings and settings to analyse.
//
// A saved portfolio's own benchmark and currency are used unless the request
// overrides them, so "analyse my portfolio" measures it against the benchmark
// the user chose for it rather than against a global default.
func (s *Service) resolve(ctx context.Context, req AnalyzeRequest) ([]Holding, string, string, string, error) {
	if id := strings.TrimSpace(req.PortfolioID); id != "" {
		saved, err := s.repo.GetPortfolio(ctx, id)
		if err != nil {
			return nil, "", "", "", err
		}
		if len(saved.Holdings) == 0 {
			return nil, "", "", "", fmt.Errorf(
				"%w: portfolio %s has no holdings, so there is nothing to analyse", ErrInvalid, id)
		}
		return saved.Holdings,
			orDefault(req.Benchmark, saved.Benchmark),
			saved.BaseCurrency,
			saved.Name,
			nil
	}
	return req.Holdings, orDefault(req.Benchmark, defaultBenchmark), defaultCurrency, "", nil
}

func (s *Service) riskRequest(
	req AnalyzeRequest, holdings []Holding, benchmark, currency, name string,
) quant.PortfolioRiskRequest {
	return quant.PortfolioRiskRequest{
		Holdings:     toQuantHoldings(holdings),
		Start:        req.Start,
		End:          req.End,
		Interval:     orDefault(req.Interval, defaultInterval),
		Benchmark:    benchmark,
		RiskFreeRate: req.RiskFreeRate,
		Name:         name,
		BaseCurrency: currency,
	}
}

// Analyze computes risk metrics, optionally persisting the result.
func (s *Service) Analyze(ctx context.Context, req AnalyzeRequest) (json.RawMessage, *Report, error) {
	if err := req.Validate(); err != nil {
		return nil, nil, err
	}
	holdings, benchmark, currency, name, err := s.resolve(ctx, req)
	if err != nil {
		return nil, nil, err
	}

	payload, err := s.analyzer.AnalyzePortfolioRisk(
		ctx, s.riskRequest(req, holdings, benchmark, currency, name))
	if err != nil {
		return nil, nil, err
	}

	report, err := s.persistIfRequested(ctx, req, KindRisk, benchmark, payload)
	if err != nil {
		return nil, nil, err
	}
	return payload, report, nil
}

// Scenario reweights a portfolio and returns the before/after comparison.
func (s *Service) Scenario(ctx context.Context, req ScenarioRequest) (json.RawMessage, *Report, error) {
	if err := req.Validate(); err != nil {
		return nil, nil, err
	}
	holdings, benchmark, currency, name, err := s.resolve(ctx, req.AnalyzeRequest)
	if err != nil {
		return nil, nil, err
	}

	payload, err := s.analyzer.RunPortfolioScenario(ctx, quant.PortfolioScenarioRequest{
		PortfolioRiskRequest: s.riskRequest(req.AnalyzeRequest, holdings, benchmark, currency, name),
		Weights:              req.Weights,
	})
	if err != nil {
		return nil, nil, err
	}

	report, err := s.persistIfRequested(ctx, req.AnalyzeRequest, KindScenario, benchmark, payload)
	if err != nil {
		return nil, nil, err
	}
	return payload, report, nil
}

func (s *Service) persistIfRequested(
	ctx context.Context, req AnalyzeRequest, kind, benchmark string, payload json.RawMessage,
) (*Report, error) {
	if !req.SaveReport {
		return nil, nil
	}
	report := Report{
		ID:          newID("rr"),
		PortfolioID: strings.TrimSpace(req.PortfolioID),
		Name:        strings.TrimSpace(req.SaveAs),
		Kind:        kind,
		Benchmark:   benchmark,
		Interval:    orDefault(req.Interval, defaultInterval),
		Start:       req.Start,
		End:         req.End,
		Payload:     payload,
		CreatedAt:   time.Now().UTC(),
	}
	projectReportFields(&report)

	if err := s.repo.SaveReport(ctx, report); err != nil {
		return nil, fmt.Errorf("persist risk report: %w", err)
	}
	return &report, nil
}

// projectReportFields lifts the list-view columns out of the engine's report.
//
// For a scenario the "before" state is projected, so a report list compares
// like with like: every row is the portfolio as it actually stood, and the
// reweighting is inside the payload for anyone who opens it.
//
// A field the engine did not report stays nil rather than becoming zero. The
// authoritative report is intact either way, so a projection that cannot be
// read is not a reason to fail the whole request.
func projectReportFields(report *Report) {
	var view struct {
		AsOf    string `json:"as_of"`
		Metrics struct {
			TotalValue           *float64 `json:"total_value"`
			AnnualizedReturn     *float64 `json:"annualized_return"`
			AnnualizedVolatility *float64 `json:"annualized_volatility"`
			Beta                 *float64 `json:"beta"`
			Sharpe               *float64 `json:"sharpe"`
			MaxDrawdown          *float64 `json:"max_drawdown"`
		} `json:"metrics"`
		Before struct {
			AsOf    string `json:"as_of"`
			Metrics struct {
				TotalValue           *float64 `json:"total_value"`
				AnnualizedReturn     *float64 `json:"annualized_return"`
				AnnualizedVolatility *float64 `json:"annualized_volatility"`
				Beta                 *float64 `json:"beta"`
				Sharpe               *float64 `json:"sharpe"`
				MaxDrawdown          *float64 `json:"max_drawdown"`
			} `json:"metrics"`
		} `json:"before"`
	}
	if err := json.Unmarshal(report.Payload, &view); err != nil {
		return
	}

	metrics := view.Metrics
	asOf := view.AsOf
	if report.Kind == KindScenario {
		metrics = view.Before.Metrics
		if view.Before.AsOf != "" {
			asOf = view.Before.AsOf
		}
	}

	report.AsOf = asOf
	report.Metrics = Metrics{
		TotalValue:           metrics.TotalValue,
		AnnualizedReturn:     metrics.AnnualizedReturn,
		AnnualizedVolatility: metrics.AnnualizedVolatility,
		Beta:                 metrics.Beta,
		Sharpe:               metrics.Sharpe,
		MaxDrawdown:          metrics.MaxDrawdown,
	}
}

func toQuantHoldings(holdings []Holding) []quant.Holding {
	out := make([]quant.Holding, 0, len(holdings))
	for _, h := range holdings {
		out = append(out, quant.Holding{
			Symbol: h.Symbol, Quantity: h.Quantity, CostBasis: h.CostBasis,
		})
	}
	return out
}

func clampLimit(limit int) int {
	if limit <= 0 || limit > 200 {
		return 50
	}
	return limit
}

func orDefault(value, fallback string) string {
	if strings.TrimSpace(value) == "" {
		return fallback
	}
	return value
}

func newID(prefix string) string {
	var b [10]byte
	if _, err := rand.Read(b[:]); err != nil {
		return prefix + "_" + time.Now().UTC().Format("20060102150405.000000000")
	}
	return prefix + "_" + hex.EncodeToString(b[:])
}

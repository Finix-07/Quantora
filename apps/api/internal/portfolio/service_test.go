package portfolio

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"strings"
	"testing"

	"github.com/anubhavjha/ai-quant-terminal/apps/api/internal/quant"
)

// --- doubles -----------------------------------------------------------------

type fakeRepo struct {
	portfolios map[string]Portfolio
	reports    map[string]Report
}

func newFakeRepo() *fakeRepo {
	return &fakeRepo{portfolios: map[string]Portfolio{}, reports: map[string]Report{}}
}

func (r *fakeRepo) SavePortfolio(_ context.Context, p Portfolio) error {
	r.portfolios[p.ID] = p
	return nil
}

func (r *fakeRepo) GetPortfolio(_ context.Context, id string) (Portfolio, error) {
	p, ok := r.portfolios[id]
	if !ok {
		return Portfolio{}, fmt.Errorf("%w: %s", ErrNotFound, id)
	}
	return p, nil
}

func (r *fakeRepo) ListPortfolios(_ context.Context, _ int) ([]Summary, error) { return nil, nil }

func (r *fakeRepo) DeletePortfolio(_ context.Context, id string) error {
	if _, ok := r.portfolios[id]; !ok {
		return fmt.Errorf("%w: %s", ErrNotFound, id)
	}
	delete(r.portfolios, id)
	return nil
}

func (r *fakeRepo) SaveReport(_ context.Context, report Report) error {
	r.reports[report.ID] = report
	return nil
}

func (r *fakeRepo) GetReport(_ context.Context, id string) (Report, error) {
	report, ok := r.reports[id]
	if !ok {
		return Report{}, fmt.Errorf("%w: %s", ErrNotFound, id)
	}
	return report, nil
}

func (r *fakeRepo) ListReports(_ context.Context, _ ReportFilter) ([]ReportSummary, error) {
	return nil, nil
}

type fakeAnalyzer struct {
	riskPayload     string
	scenarioPayload string
	lastRisk        quant.PortfolioRiskRequest
	lastScenario    quant.PortfolioScenarioRequest
	riskCalls       int
	scenarioCalls   int
	err             error
}

func (f *fakeAnalyzer) AnalyzePortfolioRisk(_ context.Context, req quant.PortfolioRiskRequest) (json.RawMessage, error) {
	f.riskCalls++
	f.lastRisk = req
	if f.err != nil {
		return nil, f.err
	}
	return json.RawMessage(f.riskPayload), nil
}

func (f *fakeAnalyzer) RunPortfolioScenario(_ context.Context, req quant.PortfolioScenarioRequest) (json.RawMessage, error) {
	f.scenarioCalls++
	f.lastScenario = req
	if f.err != nil {
		return nil, f.err
	}
	return json.RawMessage(f.scenarioPayload), nil
}

const riskPayload = `{
  "as_of": "2024-12-31",
  "metrics": {"total_value": 380014.07, "annualized_return": 0.0582,
              "annualized_volatility": 0.1625, "beta": 0.969, "sharpe": 0.4295,
              "max_drawdown": -0.1617, "sortino": null,
              "unavailable": {"sortino": "No downside deviation in the period."}}
}`

// The scenario payload's "after" metrics differ from "before" on purpose: the
// projection must be shown to pick the right one rather than happening to.
const scenarioPayload = `{
  "as_of": "2024-12-31",
  "before": {"as_of": "2024-12-31",
             "metrics": {"total_value": 380014.07, "annualized_return": 0.0582,
                         "annualized_volatility": 0.1625, "beta": 0.969,
                         "sharpe": 0.4295, "max_drawdown": -0.1617}},
  "after":  {"metrics": {"total_value": 380014.07, "annualized_return": 0.0660,
                         "annualized_volatility": 0.1633, "beta": 1.0069,
                         "sharpe": 0.4728, "max_drawdown": -0.1500}}
}`

func newTestService(analyzer *fakeAnalyzer, repo *fakeRepo) *Service {
	return NewService(repo, analyzer)
}

func defaultAnalyzer() *fakeAnalyzer {
	return &fakeAnalyzer{riskPayload: riskPayload, scenarioPayload: scenarioPayload}
}

func saveRequest() SaveRequest {
	return SaveRequest{
		Name: "Core equity",
		Holdings: []Holding{
			{Symbol: "RELIANCE.NS", Quantity: 100},
			{Symbol: "TCS.NS", Quantity: 50},
		},
	}
}

// --- portfolios --------------------------------------------------------------

func TestSaveAppliesDefaultsAndPersists(t *testing.T) {
	repo := newFakeRepo()
	service := newTestService(defaultAnalyzer(), repo)

	saved, err := service.Save(context.Background(), saveRequest())
	if err != nil {
		t.Fatalf("Save: %v", err)
	}

	if !strings.HasPrefix(saved.ID, "pf_") {
		t.Errorf("ID = %q, want a pf_ prefix", saved.ID)
	}
	if saved.Benchmark != defaultBenchmark || saved.BaseCurrency != defaultCurrency {
		t.Errorf("defaults not applied: %+v", saved)
	}
	if _, ok := repo.portfolios[saved.ID]; !ok {
		t.Error("the portfolio was not persisted")
	}
}

func TestUpdateKeepsTheIDAndCreationTime(t *testing.T) {
	// Saved reports point at the ID, so a "rename" that minted a new one would
	// orphan every report the user had already taken.
	repo := newFakeRepo()
	service := newTestService(defaultAnalyzer(), repo)

	original, _ := service.Save(context.Background(), saveRequest())
	req := saveRequest()
	req.Name = "Renamed"
	req.Holdings = []Holding{{Symbol: "INFY.NS", Quantity: 10}}

	updated, err := service.Update(context.Background(), original.ID, req)
	if err != nil {
		t.Fatalf("Update: %v", err)
	}

	if updated.ID != original.ID {
		t.Errorf("ID changed on update: %q -> %q", original.ID, updated.ID)
	}
	if !updated.CreatedAt.Equal(original.CreatedAt) {
		t.Error("CreatedAt should survive an update")
	}
	if updated.UpdatedAt.Before(original.UpdatedAt) {
		t.Error("UpdatedAt should move forward")
	}
	if len(updated.Holdings) != 1 || updated.Holdings[0].Symbol != "INFY.NS" {
		t.Errorf("holdings were not replaced: %+v", updated.Holdings)
	}
}

func TestUpdateOfAnUnknownPortfolioIsNotFound(t *testing.T) {
	service := newTestService(defaultAnalyzer(), newFakeRepo())

	_, err := service.Update(context.Background(), "pf_missing", saveRequest())

	if !errors.Is(err, ErrNotFound) {
		t.Fatalf("error = %v, want ErrNotFound", err)
	}
}

// --- analysis ----------------------------------------------------------------

func TestAnalyzeUsesTheSavedPortfoliosOwnBenchmark(t *testing.T) {
	// "Analyse my portfolio" must measure it against the benchmark the user
	// chose for it, not against a global default.
	repo := newFakeRepo()
	analyzer := defaultAnalyzer()
	service := newTestService(analyzer, repo)

	req := saveRequest()
	req.Benchmark = "BANKNIFTY"
	saved, _ := service.Save(context.Background(), req)

	_, _, err := service.Analyze(context.Background(), AnalyzeRequest{
		PortfolioID: saved.ID, Start: "2022-01-01", End: "2024-12-31",
	})
	if err != nil {
		t.Fatalf("Analyze: %v", err)
	}

	if analyzer.lastRisk.Benchmark != "BANKNIFTY" {
		t.Errorf("Benchmark = %q, want the portfolio's own", analyzer.lastRisk.Benchmark)
	}
	if len(analyzer.lastRisk.Holdings) != 2 {
		t.Errorf("holdings = %+v, want the saved ones", analyzer.lastRisk.Holdings)
	}
}

func TestAnRequestOverrideBeatsTheSavedBenchmark(t *testing.T) {
	repo := newFakeRepo()
	analyzer := defaultAnalyzer()
	service := newTestService(analyzer, repo)

	req := saveRequest()
	req.Benchmark = "BANKNIFTY"
	saved, _ := service.Save(context.Background(), req)

	_, _, err := service.Analyze(context.Background(), AnalyzeRequest{
		PortfolioID: saved.ID, Start: "2022-01-01", End: "2024-12-31", Benchmark: "NIFTY",
	})
	if err != nil {
		t.Fatalf("Analyze: %v", err)
	}
	if analyzer.lastRisk.Benchmark != "NIFTY" {
		t.Errorf("Benchmark = %q, want the request's override", analyzer.lastRisk.Benchmark)
	}
}

func TestAnalyzeAcceptsAdHocHoldingsWithoutSaving(t *testing.T) {
	repo := newFakeRepo()
	service := newTestService(defaultAnalyzer(), repo)

	_, saved, err := service.Analyze(context.Background(), AnalyzeRequest{
		Holdings: []Holding{{Symbol: "RELIANCE.NS", Quantity: 10}},
		Start:    "2022-01-01", End: "2024-12-31",
	})
	if err != nil {
		t.Fatalf("Analyze: %v", err)
	}
	if saved != nil {
		t.Error("save_report defaults to false, so nothing should be persisted")
	}
	if len(repo.portfolios) != 0 {
		t.Error("an ad-hoc analysis must not create a portfolio")
	}
}

func TestAnalyzeRejectsBothPortfolioIDAndHoldings(t *testing.T) {
	// There would be no way to say which set the resulting report described.
	service := newTestService(defaultAnalyzer(), newFakeRepo())

	_, _, err := service.Analyze(context.Background(), AnalyzeRequest{
		PortfolioID: "pf_1",
		Holdings:    []Holding{{Symbol: "RELIANCE.NS", Quantity: 10}},
		Start:       "2022-01-01", End: "2024-12-31",
	})

	if !errors.Is(err, ErrInvalid) {
		t.Fatalf("error = %v, want ErrInvalid", err)
	}
	if !strings.Contains(err.Error(), "not both") {
		t.Errorf("error %q should explain why", err)
	}
}

func TestAnalyzeRejectsAnEmptyPortfolio(t *testing.T) {
	repo := newFakeRepo()
	service := newTestService(defaultAnalyzer(), repo)
	repo.portfolios["pf_empty"] = Portfolio{ID: "pf_empty", Name: "Empty"}

	_, _, err := service.Analyze(context.Background(), AnalyzeRequest{
		PortfolioID: "pf_empty", Start: "2022-01-01", End: "2024-12-31",
	})

	if !errors.Is(err, ErrInvalid) {
		t.Fatalf("error = %v, want ErrInvalid", err)
	}
	if !strings.Contains(err.Error(), "nothing to analyse") {
		t.Errorf("error %q should say why", err)
	}
}

func TestValidationHappensBeforeTheEngineIsCalled(t *testing.T) {
	analyzer := defaultAnalyzer()
	service := newTestService(analyzer, newFakeRepo())

	_, _, err := service.Analyze(context.Background(), AnalyzeRequest{Start: "nope", End: "nope"})

	if !errors.Is(err, ErrInvalid) {
		t.Fatalf("error = %v, want ErrInvalid", err)
	}
	if analyzer.riskCalls != 0 {
		t.Error("an invalid request must not reach the engine")
	}
}

// --- report persistence ------------------------------------------------------

func TestSaveReportProjectsTheMetricsTheListShows(t *testing.T) {
	repo := newFakeRepo()
	service := newTestService(defaultAnalyzer(), repo)

	_, saved, err := service.Analyze(context.Background(), AnalyzeRequest{
		Holdings: []Holding{{Symbol: "RELIANCE.NS", Quantity: 10}},
		Start:    "2022-01-01", End: "2024-12-31",
		SaveReport: true, SaveAs: "walkthrough",
	})
	if err != nil {
		t.Fatalf("Analyze: %v", err)
	}
	if saved == nil {
		t.Fatal("save_report was true but nothing was returned")
	}

	if saved.Kind != KindRisk {
		t.Errorf("Kind = %q, want %q", saved.Kind, KindRisk)
	}
	if saved.AsOf != "2024-12-31" {
		t.Errorf("AsOf = %q", saved.AsOf)
	}
	if saved.Metrics.Beta == nil || *saved.Metrics.Beta != 0.969 {
		t.Errorf("Beta = %v, want 0.969 projected", saved.Metrics.Beta)
	}
	// The full report is stored verbatim, not rebuilt from the projection.
	var payload map[string]any
	if err := json.Unmarshal(saved.Payload, &payload); err != nil {
		t.Fatalf("stored payload is not the engine's: %v", err)
	}
	if _, ok := payload["metrics"]; !ok {
		t.Error("the payload lost fields")
	}
}

func TestScenarioProjectsTheBeforeStateNotTheAfter(t *testing.T) {
	// A report list must compare like with like: every row is the portfolio as
	// it actually stood, with the reweighting inside the payload.
	repo := newFakeRepo()
	service := newTestService(defaultAnalyzer(), repo)

	_, saved, err := service.Scenario(context.Background(), ScenarioRequest{
		AnalyzeRequest: AnalyzeRequest{
			Holdings: []Holding{{Symbol: "RELIANCE.NS", Quantity: 10}},
			Start:    "2022-01-01", End: "2024-12-31", SaveReport: true,
		},
		Weights: map[string]float64{"RELIANCE.NS": 1.0},
	})
	if err != nil {
		t.Fatalf("Scenario: %v", err)
	}

	if saved.Kind != KindScenario {
		t.Errorf("Kind = %q, want %q", saved.Kind, KindScenario)
	}
	if saved.Metrics.Beta == nil || *saved.Metrics.Beta != 0.969 {
		t.Errorf("Beta = %v, want the BEFORE beta 0.969, not the after 1.0069", saved.Metrics.Beta)
	}
	if saved.Metrics.Sharpe == nil || *saved.Metrics.Sharpe != 0.4295 {
		t.Errorf("Sharpe = %v, want the before value", saved.Metrics.Sharpe)
	}
}

func TestAnUnavailableMetricStaysNilInTheProjection(t *testing.T) {
	// A beta of 0 claims the portfolio does not move with the market. nil is
	// the absence of a measurement, and the two must not be confused (NFR5.6).
	repo := newFakeRepo()
	analyzer := &fakeAnalyzer{
		riskPayload: `{"as_of":"2024-12-31","metrics":{"total_value":1000,"beta":null,
		               "unavailable":{"beta":"The benchmark has zero variance over this window."}}}`,
	}
	service := newTestService(analyzer, repo)

	_, saved, err := service.Analyze(context.Background(), AnalyzeRequest{
		Holdings: []Holding{{Symbol: "RELIANCE.NS", Quantity: 10}},
		Start:    "2022-01-01", End: "2024-12-31", SaveReport: true,
	})
	if err != nil {
		t.Fatalf("Analyze: %v", err)
	}
	if saved.Metrics.Beta != nil {
		t.Errorf("Beta = %v, want nil for an unavailable metric", *saved.Metrics.Beta)
	}
	if saved.Metrics.TotalValue == nil || *saved.Metrics.TotalValue != 1000 {
		t.Error("an available metric alongside it should still project")
	}
}

func TestAProjectionThatCannotBeReadDoesNotFailTheRequest(t *testing.T) {
	// The authoritative report is intact; refusing to return it because a
	// list-view column could not be extracted would be the wrong trade.
	repo := newFakeRepo()
	analyzer := &fakeAnalyzer{riskPayload: `"not an object"`}
	service := newTestService(analyzer, repo)

	payload, saved, err := service.Analyze(context.Background(), AnalyzeRequest{
		Holdings: []Holding{{Symbol: "RELIANCE.NS", Quantity: 10}},
		Start:    "2022-01-01", End: "2024-12-31", SaveReport: true,
	})
	if err != nil {
		t.Fatalf("Analyze: %v", err)
	}
	if string(payload) != `"not an object"` {
		t.Error("the engine's payload should be returned unchanged")
	}
	if saved.Metrics.Beta != nil {
		t.Error("an unreadable projection must not invent values")
	}
}

func TestUpstreamErrorsPropagate(t *testing.T) {
	upstream := &quant.UpstreamError{
		StatusCode: 422, Code: "data_validation_failed",
		Message: "RELIANCE.NS and NIFTY overlap on only 3 bars.",
	}
	service := newTestService(&fakeAnalyzer{err: upstream}, newFakeRepo())

	_, _, err := service.Analyze(context.Background(), AnalyzeRequest{
		Holdings: []Holding{{Symbol: "RELIANCE.NS", Quantity: 10}},
		Start:    "2022-01-01", End: "2024-12-31",
	})

	var got *quant.UpstreamError
	if !errors.As(err, &got) {
		t.Fatalf("error = %v, want the upstream error preserved", err)
	}
	if !strings.Contains(got.Message, "overlap on only 3 bars") {
		t.Errorf("message = %q, want the engine's explanation preserved", got.Message)
	}
}

func TestScenarioRequiresWeights(t *testing.T) {
	analyzer := defaultAnalyzer()
	service := newTestService(analyzer, newFakeRepo())

	_, _, err := service.Scenario(context.Background(), ScenarioRequest{
		AnalyzeRequest: AnalyzeRequest{
			Holdings: []Holding{{Symbol: "RELIANCE.NS", Quantity: 10}},
			Start:    "2022-01-01", End: "2024-12-31",
		},
	})

	if !errors.Is(err, ErrInvalid) {
		t.Fatalf("error = %v, want ErrInvalid", err)
	}
	if analyzer.scenarioCalls != 0 {
		t.Error("an invalid scenario must not reach the engine")
	}
}

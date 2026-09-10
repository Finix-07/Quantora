package postgres

import (
	"context"
	"encoding/json"
	"errors"
	"strings"
	"testing"
	"time"

	"github.com/anubhavjha/ai-quant-terminal/apps/api/internal/portfolio"
)

// Against a real PostgreSQL instance (testing.md §2.3): the transactional
// replace, the schema's own CHECK constraints and the cascade are exactly what
// a mock would fail to verify.

func newPortfolioRepo(t *testing.T) *PortfolioRepository {
	t.Helper()
	pool := testPool(t)
	repo := NewPortfolioRepository(pool)
	t.Cleanup(func() {
		_, _ = pool.Exec(context.Background(),
			`DELETE FROM portfolios WHERE id LIKE 'pf_test_%'`)
	})
	return repo
}

func costBasis(v float64) *float64 { return &v }

func samplePortfolio(id string) portfolio.Portfolio {
	now := time.Now().UTC().Truncate(time.Microsecond)
	return portfolio.Portfolio{
		ID:           id,
		Name:         "Core equity",
		Description:  "saved by a test",
		BaseCurrency: "INR",
		Benchmark:    "NIFTY",
		Holdings: []portfolio.Holding{
			{Symbol: "RELIANCE.NS", Quantity: 100, CostBasis: costBasis(1200)},
			{Symbol: "TCS.NS", Quantity: 50},
		},
		CreatedAt: now,
		UpdatedAt: now,
	}
}

func TestPortfolioSaveGetRoundTrip(t *testing.T) {
	repo := newPortfolioRepo(t)
	ctx := context.Background()
	original := samplePortfolio("pf_test_roundtrip")

	if err := repo.SavePortfolio(ctx, original); err != nil {
		t.Fatalf("SavePortfolio: %v", err)
	}
	loaded, err := repo.GetPortfolio(ctx, original.ID)
	if err != nil {
		t.Fatalf("GetPortfolio: %v", err)
	}

	if loaded.Name != original.Name || loaded.Benchmark != original.Benchmark {
		t.Errorf("header did not round-trip: %+v", loaded)
	}
	if len(loaded.Holdings) != 2 {
		t.Fatalf("got %d holdings, want 2", len(loaded.Holdings))
	}
	// An unrecorded cost basis must stay distinguishable from a cost of zero:
	// the engine reports no unrealised P&L for the first and a 100% gain for
	// the second.
	bySymbol := map[string]portfolio.Holding{}
	for _, h := range loaded.Holdings {
		bySymbol[h.Symbol] = h
	}
	if bySymbol["TCS.NS"].CostBasis != nil {
		t.Errorf("an unrecorded cost basis came back as %v, want nil", *bySymbol["TCS.NS"].CostBasis)
	}
	if bySymbol["RELIANCE.NS"].CostBasis == nil || *bySymbol["RELIANCE.NS"].CostBasis != 1200 {
		t.Errorf("cost basis did not round-trip: %v", bySymbol["RELIANCE.NS"].CostBasis)
	}
}

func TestSavingReplacesPositionsRatherThanMerging(t *testing.T) {
	// A holding the user removed must disappear. An update that only ever added
	// would leave a sold position quietly contributing to every future report.
	repo := newPortfolioRepo(t)
	ctx := context.Background()
	p := samplePortfolio("pf_test_replace")
	if err := repo.SavePortfolio(ctx, p); err != nil {
		t.Fatalf("SavePortfolio: %v", err)
	}

	p.Holdings = []portfolio.Holding{{Symbol: "INFY.NS", Quantity: 10}}
	if err := repo.SavePortfolio(ctx, p); err != nil {
		t.Fatalf("SavePortfolio (update): %v", err)
	}

	loaded, err := repo.GetPortfolio(ctx, p.ID)
	if err != nil {
		t.Fatalf("GetPortfolio: %v", err)
	}
	if len(loaded.Holdings) != 1 || loaded.Holdings[0].Symbol != "INFY.NS" {
		t.Errorf("holdings = %+v, want only the new one", loaded.Holdings)
	}
}

func TestGetUnknownPortfolioIsNotFound(t *testing.T) {
	repo := newPortfolioRepo(t)

	_, err := repo.GetPortfolio(context.Background(), "pf_test_missing")

	if !errors.Is(err, portfolio.ErrNotFound) {
		t.Fatalf("error = %v, want portfolio.ErrNotFound", err)
	}
}

func TestEmptyPortfolioMarshalsHoldingsAsAnArray(t *testing.T) {
	// null would make a client iterating the field crash.
	repo := newPortfolioRepo(t)
	ctx := context.Background()
	p := samplePortfolio("pf_test_empty")
	p.Holdings = nil
	if err := repo.SavePortfolio(ctx, p); err != nil {
		t.Fatalf("SavePortfolio: %v", err)
	}

	loaded, err := repo.GetPortfolio(ctx, p.ID)
	if err != nil {
		t.Fatalf("GetPortfolio: %v", err)
	}
	encoded, err := json.Marshal(loaded)
	if err != nil {
		t.Fatalf("marshal: %v", err)
	}
	if !strings.Contains(string(encoded), `"holdings":[]`) {
		t.Errorf("holdings should marshal as [], got %s", encoded)
	}
}

func TestListProjectsTheHoldingCountWithoutFetchingPositions(t *testing.T) {
	repo := newPortfolioRepo(t)
	ctx := context.Background()
	if err := repo.SavePortfolio(ctx, samplePortfolio("pf_test_list")); err != nil {
		t.Fatalf("SavePortfolio: %v", err)
	}

	summaries, err := repo.ListPortfolios(ctx, 50)
	if err != nil {
		t.Fatalf("ListPortfolios: %v", err)
	}

	var found *portfolio.Summary
	for i := range summaries {
		if summaries[i].ID == "pf_test_list" {
			found = &summaries[i]
		}
	}
	if found == nil {
		t.Fatal("the saved portfolio did not appear in the list")
	}
	if found.HoldingCount != 2 {
		t.Errorf("HoldingCount = %d, want 2", found.HoldingCount)
	}
}

func TestDeleteCascadesToPositionsAndReports(t *testing.T) {
	// A saved report must not survive pointing at a portfolio that no longer
	// exists.
	repo := newPortfolioRepo(t)
	ctx := context.Background()
	p := samplePortfolio("pf_test_cascade")
	if err := repo.SavePortfolio(ctx, p); err != nil {
		t.Fatalf("SavePortfolio: %v", err)
	}
	report := sampleReport("rr_test_cascade", p.ID)
	if err := repo.SaveReport(ctx, report); err != nil {
		t.Fatalf("SaveReport: %v", err)
	}

	if err := repo.DeletePortfolio(ctx, p.ID); err != nil {
		t.Fatalf("DeletePortfolio: %v", err)
	}
	if _, err := repo.GetPortfolio(ctx, p.ID); !errors.Is(err, portfolio.ErrNotFound) {
		t.Fatalf("error = %v, want ErrNotFound", err)
	}
	if _, err := repo.GetReport(ctx, report.ID); !errors.Is(err, portfolio.ErrNotFound) {
		t.Fatalf("the report should have cascaded away, got %v", err)
	}
}

func TestDatabaseRejectsANonPositiveQuantity(t *testing.T) {
	// The schema's CHECK is the last line of defence if a caller ever skips
	// service-level validation. A holding of nothing is not a holding.
	repo := newPortfolioRepo(t)
	p := samplePortfolio("pf_test_badqty")
	p.Holdings = []portfolio.Holding{{Symbol: "RELIANCE.NS", Quantity: 0}}

	if err := repo.SavePortfolio(context.Background(), p); err == nil {
		t.Fatal("the database should reject a zero quantity")
	}
}

func TestDatabaseRejectsABlankName(t *testing.T) {
	repo := newPortfolioRepo(t)
	p := samplePortfolio("pf_test_blank")
	p.Name = "   "

	if err := repo.SavePortfolio(context.Background(), p); err == nil {
		t.Fatal("the database should reject a whitespace-only name")
	}
}

func sampleReport(id, portfolioID string) portfolio.Report {
	beta := 0.969
	value := 380014.07
	return portfolio.Report{
		ID:          id,
		PortfolioID: portfolioID,
		Name:        "walkthrough",
		Kind:        portfolio.KindRisk,
		Benchmark:   "NIFTY",
		Interval:    "1d",
		Start:       "2022-01-01",
		End:         "2024-12-31",
		AsOf:        "2024-12-31",
		Metrics: portfolio.Metrics{
			TotalValue: &value,
			Beta:       &beta,
			// Sharpe stays nil: the engine could not compute it.
		},
		Payload:   json.RawMessage(`{"metrics":{"beta":0.969},"assumptions":["static weights"]}`),
		CreatedAt: time.Now().UTC().Truncate(time.Microsecond),
	}
}

func TestReportSaveGetRoundTrip(t *testing.T) {
	repo := newPortfolioRepo(t)
	ctx := context.Background()
	p := samplePortfolio("pf_test_report")
	if err := repo.SavePortfolio(ctx, p); err != nil {
		t.Fatalf("SavePortfolio: %v", err)
	}
	original := sampleReport("rr_test_roundtrip", p.ID)
	if err := repo.SaveReport(ctx, original); err != nil {
		t.Fatalf("SaveReport: %v", err)
	}

	loaded, err := repo.GetReport(ctx, original.ID)
	if err != nil {
		t.Fatalf("GetReport: %v", err)
	}

	if loaded.Kind != portfolio.KindRisk || loaded.Benchmark != "NIFTY" {
		t.Errorf("header did not round-trip: %+v", loaded)
	}
	if loaded.Metrics.Beta == nil || *loaded.Metrics.Beta != 0.969 {
		t.Errorf("Beta = %v", loaded.Metrics.Beta)
	}
	// A metric the engine could not compute must come back nil, not 0.
	if loaded.Metrics.Sharpe != nil {
		t.Errorf("Sharpe = %v, want nil", *loaded.Metrics.Sharpe)
	}
	var payload map[string]any
	if err := json.Unmarshal(loaded.Payload, &payload); err != nil {
		t.Fatalf("payload is not valid JSON: %v", err)
	}
	if _, ok := payload["assumptions"]; !ok {
		t.Error("the payload lost fields in storage")
	}
}

func TestListReportsFiltersByKind(t *testing.T) {
	repo := newPortfolioRepo(t)
	ctx := context.Background()
	p := samplePortfolio("pf_test_filter")
	if err := repo.SavePortfolio(ctx, p); err != nil {
		t.Fatalf("SavePortfolio: %v", err)
	}
	risk := sampleReport("rr_test_filter_risk", p.ID)
	scenario := sampleReport("rr_test_filter_scenario", p.ID)
	scenario.Kind = portfolio.KindScenario
	for _, r := range []portfolio.Report{risk, scenario} {
		if err := repo.SaveReport(ctx, r); err != nil {
			t.Fatalf("SaveReport: %v", err)
		}
	}

	summaries, err := repo.ListReports(ctx, portfolio.ReportFilter{
		PortfolioID: p.ID, Kind: portfolio.KindScenario, Limit: 50,
	})
	if err != nil {
		t.Fatalf("ListReports: %v", err)
	}
	if len(summaries) != 1 || summaries[0].Kind != portfolio.KindScenario {
		t.Errorf("filter leaked: %+v", summaries)
	}
}

func TestDatabaseRejectsAnUnknownReportKind(t *testing.T) {
	// 'risk' and 'scenario' are the only kinds the reader knows how to render.
	repo := newPortfolioRepo(t)
	ctx := context.Background()
	p := samplePortfolio("pf_test_kind")
	if err := repo.SavePortfolio(ctx, p); err != nil {
		t.Fatalf("SavePortfolio: %v", err)
	}
	report := sampleReport("rr_test_badkind", p.ID)
	report.Kind = "something_else"

	if err := repo.SaveReport(ctx, report); err == nil {
		t.Fatal("the database should reject an unknown report kind")
	}
}

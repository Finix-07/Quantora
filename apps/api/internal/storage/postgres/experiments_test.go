package postgres

import (
	"context"
	"encoding/json"
	"errors"
	"testing"
	"time"

	"github.com/anubhavjha/ai-quant-terminal/apps/api/internal/experiment"
)

// These run against a real PostgreSQL instance (testing.md §2.3): the JSONB
// round trip, the metric projection done in SQL, and the migration's own
// constraints are precisely what a mock would fail to verify.

func sampleExperiment(id string) experiment.Experiment {
	return experiment.Experiment{
		ID:       id,
		Name:     "MACD baseline",
		Notes:    "saved by a test",
		Strategy: "macd",
		Symbol:   "RELIANCE.NS",
		Interval: "1d",
		Start:    "2022-01-01",
		End:      "2024-12-31",
		Parameters: map[string]any{
			"fast": float64(12), "slow": float64(26), "signal": float64(9),
		},
		CostModel: map[string]any{
			"commission_bps": float64(3), "slippage_bps": float64(5),
		},
		BacktestConfig: map[string]any{
			"initial_cash": float64(1000000), "execution_model": "next_bar_open",
		},
		DataVersion:     "sha256:testfixture",
		CodeVersion:     "test",
		ContractVersion: 1,
		Result: json.RawMessage(`{
			"strategy":"macd","symbol":"RELIANCE.NS","data_version":"sha256:testfixture",
			"metrics":{"total_return":0.29,"cagr":0.09,"sharpe":0.37,"sortino":null,
			           "max_drawdown":-0.24,"win_rate":0.33,"trade_count":48},
			"equity_curve":[{"timestamp":"2022-01-03T00:00:00+05:30","value":1000000}],
			"trades":[]
		}`),
		CreatedAt: time.Now().UTC().Truncate(time.Microsecond),
	}
}

func newRepo(t *testing.T) *ExperimentRepository {
	t.Helper()
	pool := testPool(t)
	repo := NewExperimentRepository(pool)
	t.Cleanup(func() {
		_, _ = pool.Exec(context.Background(), `DELETE FROM experiments WHERE code_version = 'test'`)
	})
	return repo
}

func TestExperimentSaveGetRoundTrip(t *testing.T) {
	repo := newRepo(t)
	ctx := context.Background()
	original := sampleExperiment("exp_roundtrip_test")

	if err := repo.Save(ctx, original); err != nil {
		t.Fatalf("Save: %v", err)
	}
	loaded, err := repo.Get(ctx, original.ID)
	if err != nil {
		t.Fatalf("Get: %v", err)
	}

	if loaded.Strategy != original.Strategy || loaded.Symbol != original.Symbol {
		t.Errorf("identity fields did not round-trip: %+v", loaded)
	}
	if loaded.DataVersion != original.DataVersion {
		t.Errorf("DataVersion = %q, want %q", loaded.DataVersion, original.DataVersion)
	}
	// The JSONB columns are what make a rerun possible; they must survive
	// exactly.
	if loaded.Parameters["fast"] != float64(12) {
		t.Errorf("Parameters = %v", loaded.Parameters)
	}
	if loaded.CostModel["slippage_bps"] != float64(5) {
		t.Errorf("CostModel = %v", loaded.CostModel)
	}
	if loaded.BacktestConfig["execution_model"] != "next_bar_open" {
		t.Errorf("BacktestConfig = %v", loaded.BacktestConfig)
	}
	var result map[string]any
	if err := json.Unmarshal(loaded.Result, &result); err != nil {
		t.Fatalf("stored result is not valid JSON: %v", err)
	}
	if _, ok := result["equity_curve"]; !ok {
		t.Error("the full result payload lost fields in storage")
	}
}

func TestExperimentGetUnknownIDIsNotFound(t *testing.T) {
	repo := newRepo(t)

	_, err := repo.Get(context.Background(), "exp_does_not_exist")

	if !errors.Is(err, experiment.ErrNotFound) {
		t.Fatalf("error = %v, want experiment.ErrNotFound", err)
	}
}

func TestExperimentListProjectsMetricsWithoutTheFullPayload(t *testing.T) {
	repo := newRepo(t)
	ctx := context.Background()
	if err := repo.Save(ctx, sampleExperiment("exp_list_test")); err != nil {
		t.Fatalf("Save: %v", err)
	}

	summaries, err := repo.List(ctx, experiment.ListFilter{Symbol: "RELIANCE.NS", Limit: 10})
	if err != nil {
		t.Fatalf("List: %v", err)
	}

	var found *experiment.Summary
	for i := range summaries {
		if summaries[i].ID == "exp_list_test" {
			found = &summaries[i]
		}
	}
	if found == nil {
		t.Fatal("the saved experiment did not appear in the list")
	}
	if found.Metrics.TradeCount != 48 {
		t.Errorf("TradeCount = %d, want 48 projected from the result", found.Metrics.TradeCount)
	}
	if found.Metrics.Sharpe == nil || *found.Metrics.Sharpe != 0.37 {
		t.Errorf("Sharpe = %v, want 0.37", found.Metrics.Sharpe)
	}
	// A metric the engine could not compute must stay null, not become 0 — the
	// journal must never show a fabricated zero where the answer was "not
	// applicable".
	if found.Metrics.Sortino != nil {
		t.Errorf("Sortino = %v, want nil for an unavailable metric", *found.Metrics.Sortino)
	}
}

func TestExperimentListFiltersByStrategy(t *testing.T) {
	repo := newRepo(t)
	ctx := context.Background()

	macd := sampleExperiment("exp_filter_macd")
	other := sampleExperiment("exp_filter_other")
	other.Strategy = "bollinger_for_filter_test"
	if err := repo.Save(ctx, macd); err != nil {
		t.Fatalf("Save: %v", err)
	}
	if err := repo.Save(ctx, other); err != nil {
		t.Fatalf("Save: %v", err)
	}

	summaries, err := repo.List(ctx, experiment.ListFilter{Strategy: "bollinger_for_filter_test", Limit: 10})
	if err != nil {
		t.Fatalf("List: %v", err)
	}

	for _, summary := range summaries {
		if summary.Strategy != "bollinger_for_filter_test" {
			t.Fatalf("filter leaked a %q experiment", summary.Strategy)
		}
	}
	if len(summaries) != 1 {
		t.Errorf("got %d summaries, want exactly the filtered one", len(summaries))
	}
}

func TestExperimentDelete(t *testing.T) {
	repo := newRepo(t)
	ctx := context.Background()
	if err := repo.Save(ctx, sampleExperiment("exp_delete_test")); err != nil {
		t.Fatalf("Save: %v", err)
	}

	if err := repo.Delete(ctx, "exp_delete_test"); err != nil {
		t.Fatalf("Delete: %v", err)
	}
	if _, err := repo.Get(ctx, "exp_delete_test"); !errors.Is(err, experiment.ErrNotFound) {
		t.Fatalf("error = %v, want ErrNotFound after delete", err)
	}
	if err := repo.Delete(ctx, "exp_delete_test"); !errors.Is(err, experiment.ErrNotFound) {
		t.Fatalf("deleting twice: error = %v, want ErrNotFound", err)
	}
}

func TestExperimentDateRangeConstraintIsEnforcedByTheDatabase(t *testing.T) {
	// The migration's CHECK is the last line of defence if a caller ever skips
	// service-level validation.
	repo := newRepo(t)
	reversed := sampleExperiment("exp_bad_range_test")
	reversed.Start, reversed.End = "2024-12-31", "2022-01-01"

	if err := repo.Save(context.Background(), reversed); err == nil {
		t.Fatal("the database should reject start_date > end_date")
	}
}

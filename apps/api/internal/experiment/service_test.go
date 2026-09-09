package experiment

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"strings"
	"testing"

	"github.com/anubhavjha/ai-quant-terminal/apps/api/internal/backtest"
	"github.com/anubhavjha/ai-quant-terminal/apps/api/internal/quant"
)

// --- doubles -----------------------------------------------------------------

type fakeRepo struct {
	saved   map[string]Experiment
	saveErr error
}

func newFakeRepo() *fakeRepo { return &fakeRepo{saved: map[string]Experiment{}} }

func (r *fakeRepo) Save(_ context.Context, exp Experiment) error {
	if r.saveErr != nil {
		return r.saveErr
	}
	r.saved[exp.ID] = exp
	return nil
}

func (r *fakeRepo) Get(_ context.Context, id string) (Experiment, error) {
	exp, ok := r.saved[id]
	if !ok {
		return Experiment{}, fmt.Errorf("%w: %s", ErrNotFound, id)
	}
	return exp, nil
}

func (r *fakeRepo) List(_ context.Context, _ ListFilter) ([]Summary, error) { return nil, nil }

func (r *fakeRepo) Delete(_ context.Context, id string) error {
	if _, ok := r.saved[id]; !ok {
		return fmt.Errorf("%w: %s", ErrNotFound, id)
	}
	delete(r.saved, id)
	return nil
}

type fakeRunner struct {
	payloads []string
	calls    int
	last     quant.BacktestRequest
	err      error
}

func (f *fakeRunner) RunBacktest(_ context.Context, req quant.BacktestRequest) (json.RawMessage, error) {
	f.last = req
	if f.err != nil {
		return nil, f.err
	}
	payload := f.payloads[min(f.calls, len(f.payloads)-1)]
	f.calls++
	return json.RawMessage(payload), nil
}

type fakeBacktests struct {
	records map[string]backtest.Record
}

func (f *fakeBacktests) Get(_ context.Context, id string) (backtest.Record, error) {
	record, ok := f.records[id]
	if !ok {
		return backtest.Record{}, fmt.Errorf("%w: %s", backtest.ErrNotFound, id)
	}
	return record, nil
}

func resultJSON(dataVersion string, sharpe float64, tradeCount int) string {
	return fmt.Sprintf(`{
	  "strategy":"macd","symbol":"RELIANCE.NS","interval":"1d",
	  "start":"2022-01-01","end":"2024-12-31",
	  "parameters":{"fast":12,"slow":26,"signal":9},
	  "cost_model":{"commission_bps":3,"commission_min":0,"slippage_bps":5,"spread_bps":2},
	  "backtest_config":{"initial_cash":500000,"execution_model":"next_bar_close","allow_short":true,"liquidate_at_end":false,"risk_free_rate":0.07},
	  "data_version":"%s","code_version":"test","contract_version":1,
	  "metrics":{"total_return":0.29,"cagr":0.09,"sharpe":%v,"sortino":0.5,"max_drawdown":-0.24,
	             "win_rate":0.33,"profit_factor":1.16,"final_equity":1290000,"total_costs":118547,"trade_count":%d}
	}`, dataVersion, sharpe, tradeCount)
}

func saveRequest() SaveRequest {
	return SaveRequest{
		Name:     "MACD baseline",
		Symbol:   "RELIANCE.NS",
		Strategy: "macd",
		Start:    "2022-01-01",
		End:      "2024-12-31",
	}
}

func newTestService(runner *fakeRunner, repo *fakeRepo) *Service {
	return NewService(repo, runner, &fakeBacktests{records: map[string]backtest.Record{}})
}

// --- save --------------------------------------------------------------------

func TestSaveStoresEverythingNeededToRerun(t *testing.T) {
	repo := newFakeRepo()
	runner := &fakeRunner{payloads: []string{resultJSON("sha256:aaa", 0.37, 48)}}
	service := newTestService(runner, repo)

	saved, err := service.Save(context.Background(), saveRequest())
	if err != nil {
		t.Fatalf("Save: %v", err)
	}

	if !strings.HasPrefix(saved.ID, "exp_") {
		t.Errorf("ID = %q, want an exp_ prefix", saved.ID)
	}
	// Everything the engine needs to reproduce the run must be stored, not
	// re-derived from today's defaults at rerun time.
	if saved.DataVersion != "sha256:aaa" {
		t.Errorf("DataVersion = %q", saved.DataVersion)
	}
	if saved.Parameters["fast"] != float64(12) {
		t.Errorf("Parameters = %v, want the strategy parameters captured", saved.Parameters)
	}
	if saved.CostModel["slippage_bps"] != float64(5) {
		t.Errorf("CostModel = %v, want the cost assumptions captured", saved.CostModel)
	}
	if saved.BacktestConfig["execution_model"] != "next_bar_close" {
		t.Errorf("BacktestConfig = %v, want the execution assumptions captured", saved.BacktestConfig)
	}
	if _, ok := repo.saved[saved.ID]; !ok {
		t.Error("the experiment was not persisted")
	}
}

func TestSaveRejectsAResultThatCouldNotBeRerun(t *testing.T) {
	// Storing an experiment with no data_version would hand the user a "saved
	// experiment" that cannot deliver the one guarantee it exists for.
	repo := newFakeRepo()
	runner := &fakeRunner{payloads: []string{`{"strategy":"macd","symbol":"X","start":"2022-01-01","end":"2022-12-31"}`}}
	service := newTestService(runner, repo)

	_, err := service.Save(context.Background(), saveRequest())

	if !errors.Is(err, ErrInvalid) {
		t.Fatalf("error = %v, want ErrInvalid", err)
	}
	if !strings.Contains(err.Error(), "data_version") {
		t.Errorf("error %q should name the missing field", err)
	}
	if len(repo.saved) != 0 {
		t.Error("an unrerunnable experiment must not be persisted")
	}
}

func TestSaveValidatesBeforeRunningAnything(t *testing.T) {
	repo := newFakeRepo()
	runner := &fakeRunner{payloads: []string{resultJSON("sha256:aaa", 0.37, 48)}}
	service := newTestService(runner, repo)

	_, err := service.Save(context.Background(), SaveRequest{Name: "nothing else"})

	if !errors.Is(err, ErrInvalid) {
		t.Fatalf("error = %v, want ErrInvalid", err)
	}
	// The message must name every problem so the user fixes them in one pass.
	for _, want := range []string{"symbol is required", "strategy is required"} {
		if !strings.Contains(err.Error(), want) {
			t.Errorf("error %q should mention %q", err, want)
		}
	}
	if runner.calls != 0 {
		t.Error("an invalid request must not reach the engine")
	}
}

func TestSaveCanPromoteAnAlreadyRunBacktest(t *testing.T) {
	repo := newFakeRepo()
	runner := &fakeRunner{payloads: []string{resultJSON("sha256:aaa", 0.37, 48)}}
	backtests := &fakeBacktests{records: map[string]backtest.Record{
		"bt_1": {ID: "bt_1", Result: json.RawMessage(resultJSON("sha256:promoted", 1.5, 12))},
	}}
	service := NewService(repo, runner, backtests)

	saved, err := service.Save(context.Background(), SaveRequest{BacktestID: "bt_1", Name: "promoted"})
	if err != nil {
		t.Fatalf("Save: %v", err)
	}

	if saved.DataVersion != "sha256:promoted" {
		t.Errorf("DataVersion = %q, want the backtest's own result reused", saved.DataVersion)
	}
	if runner.calls != 0 {
		t.Error("promoting an existing backtest must not re-run it")
	}
}

func TestSaveReportsAnEvictedBacktestAsNotFound(t *testing.T) {
	service := NewService(newFakeRepo(), &fakeRunner{}, &fakeBacktests{records: map[string]backtest.Record{}})

	_, err := service.Save(context.Background(), SaveRequest{BacktestID: "bt_gone"})

	if !errors.Is(err, backtest.ErrNotFound) {
		t.Fatalf("error = %v, want backtest.ErrNotFound", err)
	}
}

// --- rerun -------------------------------------------------------------------

func TestRerunReplaysTheStoredConfigurationNotTodaysDefaults(t *testing.T) {
	repo := newFakeRepo()
	runner := &fakeRunner{payloads: []string{resultJSON("sha256:aaa", 0.37, 48)}}
	service := newTestService(runner, repo)

	saved, err := service.Save(context.Background(), saveRequest())
	if err != nil {
		t.Fatalf("Save: %v", err)
	}

	if _, err := service.Rerun(context.Background(), saved.ID); err != nil {
		t.Fatalf("Rerun: %v", err)
	}

	// A later change to a default must not silently alter what "the same
	// experiment" means, so every stored value is replayed verbatim.
	if runner.last.InitialCash != 500_000 {
		t.Errorf("InitialCash = %v, want the stored 500000", runner.last.InitialCash)
	}
	if runner.last.ExecutionModel != "next_bar_close" {
		t.Errorf("ExecutionModel = %q, want the stored value", runner.last.ExecutionModel)
	}
	if !runner.last.AllowShort {
		t.Error("AllowShort should have been replayed as true")
	}
	if runner.last.LiquidateAtEnd {
		t.Error("LiquidateAtEnd should have been replayed as false")
	}
	if runner.last.RiskFreeRate != 0.07 {
		t.Errorf("RiskFreeRate = %v, want the stored 0.07", runner.last.RiskFreeRate)
	}
	if runner.last.CostModel == nil || runner.last.CostModel.SlippageBps != 5 {
		t.Errorf("CostModel = %+v, want the stored assumptions", runner.last.CostModel)
	}
	if runner.last.Parameters["fast"] != float64(12) {
		t.Errorf("Parameters = %v, want the stored parameters", runner.last.Parameters)
	}
}

func TestRerunReportsExactReproduction(t *testing.T) {
	// NFR6: same config, same data version, same numbers.
	identical := resultJSON("sha256:aaa", 0.37, 48)
	repo := newFakeRepo()
	runner := &fakeRunner{payloads: []string{identical, identical}}
	service := newTestService(runner, repo)

	saved, _ := service.Save(context.Background(), saveRequest())
	outcome, err := service.Rerun(context.Background(), saved.ID)
	if err != nil {
		t.Fatalf("Rerun: %v", err)
	}

	if !outcome.Reproducible {
		t.Fatalf("Reproducible = false, differences = %+v", outcome.Differences)
	}
	if !outcome.DataVersionMatches || !outcome.MetricsMatch {
		t.Error("both sub-verdicts should be true for an exact reproduction")
	}
	if len(outcome.Differences) != 0 {
		t.Errorf("Differences = %+v, want none", outcome.Differences)
	}
	if !strings.Contains(outcome.Explanation, "Reproduced exactly") {
		t.Errorf("Explanation = %q", outcome.Explanation)
	}
}

func TestRerunDistinguishesRevisedDataFromACodeRegression(t *testing.T) {
	// The two need different responses, so a single "reproducible: false" would
	// leave the user unable to tell them apart.
	tests := []struct {
		name           string
		rerunPayload   string
		wantDataMatch  bool
		wantMetricsOK  bool
		wantInMessage  string
	}{
		{
			name:          "data revised, metrics moved",
			rerunPayload:  resultJSON("sha256:bbb", 0.41, 49),
			wantDataMatch: false,
			wantMetricsOK: false,
			wantInMessage: "revised history",
		},
		{
			name:          "data revised, metrics unchanged",
			rerunPayload:  resultJSON("sha256:bbb", 0.37, 48),
			wantDataMatch: false,
			wantMetricsOK: true,
			wantInMessage: "no longer backed by identical bars",
		},
		{
			name:          "same data, metrics moved",
			rerunPayload:  resultJSON("sha256:aaa", 0.41, 48),
			wantDataMatch: true,
			wantMetricsOK: false,
			wantInMessage: "treat it as a regression",
		},
	}

	for _, tc := range tests {
		t.Run(tc.name, func(t *testing.T) {
			repo := newFakeRepo()
			runner := &fakeRunner{payloads: []string{resultJSON("sha256:aaa", 0.37, 48), tc.rerunPayload}}
			service := newTestService(runner, repo)

			saved, _ := service.Save(context.Background(), saveRequest())
			outcome, err := service.Rerun(context.Background(), saved.ID)
			if err != nil {
				t.Fatalf("Rerun: %v", err)
			}

			if outcome.Reproducible {
				t.Error("Reproducible should be false")
			}
			if outcome.DataVersionMatches != tc.wantDataMatch {
				t.Errorf("DataVersionMatches = %v, want %v", outcome.DataVersionMatches, tc.wantDataMatch)
			}
			if outcome.MetricsMatch != tc.wantMetricsOK {
				t.Errorf("MetricsMatch = %v, want %v", outcome.MetricsMatch, tc.wantMetricsOK)
			}
			if !strings.Contains(outcome.Explanation, tc.wantInMessage) {
				t.Errorf("Explanation = %q, want it to mention %q", outcome.Explanation, tc.wantInMessage)
			}
		})
	}
}

func TestRerunNamesTheMetricsThatMoved(t *testing.T) {
	repo := newFakeRepo()
	runner := &fakeRunner{payloads: []string{
		resultJSON("sha256:aaa", 0.37, 48),
		resultJSON("sha256:aaa", 0.41, 51),
	}}
	service := newTestService(runner, repo)

	saved, _ := service.Save(context.Background(), saveRequest())
	outcome, _ := service.Rerun(context.Background(), saved.ID)

	moved := map[string]bool{}
	for _, diff := range outcome.Differences {
		moved[diff.Metric] = true
	}
	if !moved["sharpe"] {
		t.Errorf("Differences = %+v, want sharpe reported", outcome.Differences)
	}
	// A change in trade count with unchanged returns is exactly the silent drift
	// a regression check exists to catch.
	if !moved["trade_count"] {
		t.Errorf("Differences = %+v, want trade_count reported", outcome.Differences)
	}
}

func TestTinyFloatingPointDriftIsNotAFailure(t *testing.T) {
	// The result round-trips through JSON and PostgreSQL; bit-for-bit equality
	// is not guaranteed, and 1e-9 is far tighter than any real regression.
	repo := newFakeRepo()
	runner := &fakeRunner{payloads: []string{
		resultJSON("sha256:aaa", 0.37, 48),
		resultJSON("sha256:aaa", 0.37+1e-12, 48),
	}}
	service := newTestService(runner, repo)

	saved, _ := service.Save(context.Background(), saveRequest())
	outcome, _ := service.Rerun(context.Background(), saved.ID)

	if !outcome.Reproducible {
		t.Errorf("Reproducible = false for a 1e-12 difference: %+v", outcome.Differences)
	}
}

func TestDifferencesMarshalAsAnEmptyArrayNotNull(t *testing.T) {
	// A client iterating the field should get an empty list, not a crash.
	identical := resultJSON("sha256:aaa", 0.37, 48)
	repo := newFakeRepo()
	runner := &fakeRunner{payloads: []string{identical, identical}}
	service := newTestService(runner, repo)

	saved, _ := service.Save(context.Background(), saveRequest())
	outcome, _ := service.Rerun(context.Background(), saved.ID)

	encoded, err := json.Marshal(outcome)
	if err != nil {
		t.Fatalf("marshal: %v", err)
	}
	if !strings.Contains(string(encoded), `"differences":[]`) {
		t.Errorf("differences should marshal as [], got %s", encoded)
	}
}

func TestRerunOfAnUnknownExperimentIsNotFound(t *testing.T) {
	service := newTestService(&fakeRunner{}, newFakeRepo())

	_, err := service.Rerun(context.Background(), "exp_missing")

	if !errors.Is(err, ErrNotFound) {
		t.Fatalf("error = %v, want ErrNotFound", err)
	}
}

func TestDeleteRemovesTheExperiment(t *testing.T) {
	repo := newFakeRepo()
	runner := &fakeRunner{payloads: []string{resultJSON("sha256:aaa", 0.37, 48)}}
	service := newTestService(runner, repo)

	saved, _ := service.Save(context.Background(), saveRequest())
	if err := service.Delete(context.Background(), saved.ID); err != nil {
		t.Fatalf("Delete: %v", err)
	}

	if _, err := service.Get(context.Background(), saved.ID); !errors.Is(err, ErrNotFound) {
		t.Fatalf("error = %v, want ErrNotFound after delete", err)
	}
}

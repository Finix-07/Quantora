package experiment

import (
	"context"
	"crypto/rand"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"math"
	"sort"
	"strings"
	"time"

	"github.com/anubhavjha/ai-quant-terminal/apps/api/internal/backtest"
	"github.com/anubhavjha/ai-quant-terminal/apps/api/internal/quant"
)

// metricTolerance is how close two runs' metrics must be to count as identical.
//
// Not exact equality: the result travels through JSON and PostgreSQL, and IEEE
// doubles do not always survive that round trip bit-for-bit. 1e-9 is far tighter
// than any real difference a code or data change would produce, so it cannot
// hide a genuine regression.
const metricTolerance = 1e-9

// comparedMetrics are the numbers a reproducibility check compares. Counts and
// costs are included because a change in trade count with unchanged returns is
// exactly the kind of silent drift a regression check exists to catch.
var comparedMetrics = []string{
	"total_return", "cagr", "sharpe", "sortino", "max_drawdown",
	"win_rate", "profit_factor", "final_equity", "total_costs",
}

// Runner is the capability this service needs from the quant engine.
type Runner interface {
	RunBacktest(ctx context.Context, req quant.BacktestRequest) (json.RawMessage, error)
}

// BacktestLookup lets a caller promote a backtest already run this session into
// a saved experiment, instead of re-running it.
type BacktestLookup interface {
	Get(ctx context.Context, id string) (backtest.Record, error)
}

// Service implements the experiment lifecycle.
type Service struct {
	repo      Repository
	runner    Runner
	backtests BacktestLookup
}

func NewService(repo Repository, runner Runner, backtests BacktestLookup) *Service {
	return &Service{repo: repo, runner: runner, backtests: backtests}
}

// Save persists a run as a rerunnable experiment.
func (s *Service) Save(ctx context.Context, req SaveRequest) (Experiment, error) {
	if err := req.Validate(); err != nil {
		return Experiment{}, err
	}

	var result json.RawMessage
	if id := strings.TrimSpace(req.BacktestID); id != "" {
		record, err := s.backtests.Get(ctx, id)
		if err != nil {
			return Experiment{}, err
		}
		result = record.Result
	} else {
		var err error
		result, err = s.runner.RunBacktest(ctx, quant.BacktestRequest{
			Symbol:         req.Symbol,
			Strategy:       req.Strategy,
			Start:          req.Start,
			End:            req.End,
			Interval:       orDefault(req.Interval, "1d"),
			Parameters:     req.Parameters,
			InitialCash:    positiveOr(req.InitialCash, 1_000_000),
			CostModel:      req.CostModel,
			ExecutionModel: orDefault(req.ExecutionModel, "next_bar_open"),
			AllowShort:     req.AllowShort,
			LiquidateAtEnd: req.LiquidateAtEnd == nil || *req.LiquidateAtEnd,
			RiskFreeRate:   req.RiskFreeRate,
		})
		if err != nil {
			return Experiment{}, err
		}
	}

	exp, err := experimentFromResult(result)
	if err != nil {
		return Experiment{}, err
	}
	exp.ID = newID()
	exp.Name = strings.TrimSpace(req.Name)
	exp.Notes = strings.TrimSpace(req.Notes)
	exp.CreatedAt = time.Now().UTC()

	if err := s.repo.Save(ctx, exp); err != nil {
		return Experiment{}, fmt.Errorf("persist experiment: %w", err)
	}
	return exp, nil
}

// Get returns one saved experiment.
func (s *Service) Get(ctx context.Context, id string) (Experiment, error) {
	return s.repo.Get(ctx, id)
}

// List returns the journal view.
func (s *Service) List(ctx context.Context, filter ListFilter) ([]Summary, error) {
	if filter.Limit <= 0 || filter.Limit > 200 {
		filter.Limit = 50
	}
	return s.repo.List(ctx, filter)
}

// Delete removes a saved experiment.
func (s *Service) Delete(ctx context.Context, id string) error {
	return s.repo.Delete(ctx, id)
}

// Rerun re-executes a saved experiment from its stored configuration.
//
// The user supplies nothing but the ID — reconfiguring by hand is exactly what
// FR5 says a rerun must not require. The stored configuration is replayed
// verbatim rather than merged with today's defaults, so a later change to a
// default cannot silently alter what "the same experiment" means.
func (s *Service) Rerun(ctx context.Context, id string) (RerunOutcome, error) {
	original, err := s.repo.Get(ctx, id)
	if err != nil {
		return RerunOutcome{}, err
	}

	req := quant.BacktestRequest{
		Symbol:       original.Symbol,
		Strategy:     original.Strategy,
		Start:        original.Start,
		End:          original.End,
		Interval:     orDefault(original.Interval, "1d"),
		Parameters:   original.Parameters,
		ExperimentID: original.ID,
	}
	applyStoredConfig(&req, original)

	rerun, err := s.runner.RunBacktest(ctx, req)
	if err != nil {
		return RerunOutcome{}, err
	}

	return compareRuns(original, rerun), nil
}

// applyStoredConfig replays the saved backtest configuration.
//
// Anything missing falls back to the engine's default rather than to a value
// invented here, and the absence is visible in the stored config the UI shows
// alongside the result.
func applyStoredConfig(req *quant.BacktestRequest, exp Experiment) {
	config := exp.BacktestConfig
	if cash, ok := config["initial_cash"].(float64); ok && cash > 0 {
		req.InitialCash = cash
	}
	if model, ok := config["execution_model"].(string); ok && model != "" {
		req.ExecutionModel = model
	}
	if allow, ok := config["allow_short"].(bool); ok {
		req.AllowShort = allow
	}
	if liquidate, ok := config["liquidate_at_end"].(bool); ok {
		req.LiquidateAtEnd = liquidate
	} else {
		req.LiquidateAtEnd = true
	}
	if rate, ok := config["risk_free_rate"].(float64); ok {
		req.RiskFreeRate = rate
	}
	if len(exp.CostModel) > 0 {
		req.CostModel = &quant.CostModel{
			CommissionBps: numberOr(exp.CostModel, "commission_bps", 3),
			CommissionMin: numberOr(exp.CostModel, "commission_min", 0),
			SlippageBps:   numberOr(exp.CostModel, "slippage_bps", 5),
			SpreadBps:     numberOr(exp.CostModel, "spread_bps", 2),
		}
	}
}

func compareRuns(original Experiment, rerun json.RawMessage) RerunOutcome {
	outcome := RerunOutcome{
		ExperimentID:        original.ID,
		OriginalDataVersion: original.DataVersion,
		OriginalResult:      original.Result,
		RerunResult:         rerun,
	}

	var rerunView struct {
		DataVersion string         `json:"data_version"`
		Metrics     map[string]any `json:"metrics"`
	}
	_ = json.Unmarshal(rerun, &rerunView)
	outcome.RerunDataVersion = rerunView.DataVersion
	outcome.DataVersionMatches = original.DataVersion != "" &&
		original.DataVersion == rerunView.DataVersion

	originalMetrics := metricsMap(original.Result)
	outcome.Differences = diffMetrics(originalMetrics, rerunView.Metrics)
	outcome.MetricsMatch = len(outcome.Differences) == 0
	outcome.Reproducible = outcome.DataVersionMatches && outcome.MetricsMatch
	outcome.Explanation = explain(outcome)
	return outcome
}

// explain states the verdict in the terms the user has to act on. The three
// failure modes need different responses, so they are never merged into one
// message.
func explain(outcome RerunOutcome) string {
	switch {
	case outcome.Reproducible:
		return "Reproduced exactly: the source data is unchanged and every compared metric matches."
	case !outcome.DataVersionMatches && outcome.MetricsMatch:
		return "The metrics match, but the source data has changed since this experiment was saved " +
			"(the provider revised history). The result is still consistent, but it is no longer " +
			"backed by identical bars."
	case !outcome.DataVersionMatches:
		return "The source data has changed since this experiment was saved, so the differing " +
			"metrics are explained by revised history rather than by a change in the code. " +
			"Compare the data versions before treating this as a regression."
	default:
		return "The source data is identical but the metrics differ. That points at a change in " +
			"the engine or the strategy, not at the data — treat it as a regression."
	}
}

func metricsMap(result json.RawMessage) map[string]any {
	var view struct {
		Metrics map[string]any `json:"metrics"`
	}
	if err := json.Unmarshal(result, &view); err != nil {
		return nil
	}
	return view.Metrics
}

func diffMetrics(original, rerun map[string]any) []MetricDiff {
	// Non-nil so it marshals as [] rather than null: a client iterating the
	// field should get an empty list when nothing differed, not a crash.
	diffs := []MetricDiff{}
	for _, name := range comparedMetrics {
		a, aOK := asFloat(original[name])
		b, bOK := asFloat(rerun[name])
		switch {
		case !aOK && !bOK:
			// Both unavailable for the same reason; not a difference.
			continue
		case aOK != bOK:
			diffs = append(diffs, MetricDiff{Metric: name, Original: floatPtr(a, aOK), Rerun: floatPtr(b, bOK)})
		case math.Abs(a-b) > metricTolerance:
			diffs = append(diffs, MetricDiff{Metric: name, Original: &a, Rerun: &b})
		}
	}
	if a, aOK := asFloat(original["trade_count"]); aOK {
		if b, bOK := asFloat(rerun["trade_count"]); bOK && a != b {
			diffs = append(diffs, MetricDiff{Metric: "trade_count", Original: &a, Rerun: &b})
		}
	}
	sort.Slice(diffs, func(i, j int) bool { return diffs[i].Metric < diffs[j].Metric })
	return diffs
}

func asFloat(value any) (float64, bool) {
	switch v := value.(type) {
	case float64:
		return v, true
	case int:
		return float64(v), true
	case json.Number:
		f, err := v.Float64()
		return f, err == nil
	default:
		return 0, false
	}
}

func floatPtr(value float64, ok bool) *float64 {
	if !ok {
		return nil
	}
	return &value
}

// experimentFromResult reads the engine's result contract into the domain model.
//
// It fails loudly when required fields are absent: an experiment missing its
// data_version or its configuration is not rerunnable, and storing it would
// hand the user a "saved experiment" that cannot deliver the one guarantee it
// exists for (NFR6).
func experimentFromResult(result json.RawMessage) (Experiment, error) {
	var view struct {
		Strategy        string         `json:"strategy"`
		Symbol          string         `json:"symbol"`
		Interval        string         `json:"interval"`
		Start           string         `json:"start"`
		End             string         `json:"end"`
		Parameters      map[string]any `json:"parameters"`
		CostModel       map[string]any `json:"cost_model"`
		BacktestConfig  map[string]any `json:"backtest_config"`
		DataVersion     string         `json:"data_version"`
		CodeVersion     string         `json:"code_version"`
		ContractVersion int            `json:"contract_version"`
	}
	if err := json.Unmarshal(result, &view); err != nil {
		return Experiment{}, fmt.Errorf("%w: the backtest result could not be read: %v", ErrInvalid, err)
	}

	var missing []string
	if view.Strategy == "" {
		missing = append(missing, "strategy")
	}
	if view.Symbol == "" {
		missing = append(missing, "symbol")
	}
	if view.Start == "" || view.End == "" {
		missing = append(missing, "start/end")
	}
	if view.DataVersion == "" {
		missing = append(missing, "data_version")
	}
	if len(missing) > 0 {
		return Experiment{}, fmt.Errorf(
			"%w: the backtest result is missing %s, so it could not be rerun later",
			ErrInvalid, strings.Join(missing, ", "))
	}

	return Experiment{
		Strategy:        view.Strategy,
		Symbol:          view.Symbol,
		Interval:        orDefault(view.Interval, "1d"),
		Start:           view.Start,
		End:             view.End,
		Parameters:      orEmpty(view.Parameters),
		CostModel:       orEmpty(view.CostModel),
		BacktestConfig:  orEmpty(view.BacktestConfig),
		DataVersion:     view.DataVersion,
		CodeVersion:     orDefault(view.CodeVersion, "dev"),
		ContractVersion: max(view.ContractVersion, 1),
		Result:          result,
	}, nil
}

func numberOr(source map[string]any, key string, fallback float64) float64 {
	if value, ok := asFloat(source[key]); ok {
		return value
	}
	return fallback
}

func orDefault(value, fallback string) string {
	if strings.TrimSpace(value) == "" {
		return fallback
	}
	return value
}

func orEmpty(value map[string]any) map[string]any {
	if value == nil {
		return map[string]any{}
	}
	return value
}

func positiveOr(value, fallback float64) float64 {
	if value <= 0 {
		return fallback
	}
	return value
}

func newID() string {
	var b [10]byte
	if _, err := rand.Read(b[:]); err != nil {
		return "exp_" + time.Now().UTC().Format("20060102150405.000000000")
	}
	return "exp_" + hex.EncodeToString(b[:])
}

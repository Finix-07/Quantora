package postgres

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"strings"

	"github.com/jackc/pgx/v5"
	"github.com/jackc/pgx/v5/pgxpool"

	"github.com/anubhavjha/ai-quant-terminal/apps/api/internal/experiment"
)

// ExperimentRepository is the PostgreSQL implementation of
// experiment.Repository.
//
// Domain types cross the boundary in both directions: no pgx row, no SQL
// fragment and no driver type escapes this file (architecture.md §15). That is
// what lets the schema change without the service noticing.
type ExperimentRepository struct {
	pool *pgxpool.Pool
}

func NewExperimentRepository(pool *pgxpool.Pool) *ExperimentRepository {
	return &ExperimentRepository{pool: pool}
}

const insertExperiment = `
INSERT INTO experiments (
    id, name, notes, strategy, parameters, symbol, universe, interval,
    start_date, end_date, cost_model, backtest_config,
    data_version, code_version, contract_version, result, created_at
) VALUES (
    $1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14, $15, $16, $17
)`

func (r *ExperimentRepository) Save(ctx context.Context, exp experiment.Experiment) error {
	parameters, err := marshalObject(exp.Parameters)
	if err != nil {
		return fmt.Errorf("encode parameters: %w", err)
	}
	costModel, err := marshalObject(exp.CostModel)
	if err != nil {
		return fmt.Errorf("encode cost_model: %w", err)
	}
	config, err := marshalObject(exp.BacktestConfig)
	if err != nil {
		return fmt.Errorf("encode backtest_config: %w", err)
	}

	_, err = r.pool.Exec(ctx, insertExperiment,
		exp.ID,
		nullIfEmpty(exp.Name),
		nullIfEmpty(exp.Notes),
		exp.Strategy,
		parameters,
		exp.Symbol,
		nullIfEmpty(exp.Universe),
		exp.Interval,
		exp.Start,
		exp.End,
		costModel,
		config,
		exp.DataVersion,
		exp.CodeVersion,
		exp.ContractVersion,
		[]byte(exp.Result),
		exp.CreatedAt,
	)
	if err != nil {
		return fmt.Errorf("insert experiment %s: %w", exp.ID, err)
	}
	return nil
}

const selectExperiment = `
SELECT id, COALESCE(name, ''), COALESCE(notes, ''), strategy, parameters, symbol,
       COALESCE(universe, ''), interval,
       to_char(start_date, 'YYYY-MM-DD'), to_char(end_date, 'YYYY-MM-DD'),
       cost_model, backtest_config, data_version, code_version, contract_version,
       result, created_at
FROM experiments WHERE id = $1`

func (r *ExperimentRepository) Get(ctx context.Context, id string) (experiment.Experiment, error) {
	var (
		exp                           experiment.Experiment
		parameters, costModel, config []byte
		result                        []byte
	)
	err := r.pool.QueryRow(ctx, selectExperiment, id).Scan(
		&exp.ID, &exp.Name, &exp.Notes, &exp.Strategy, &parameters, &exp.Symbol,
		&exp.Universe, &exp.Interval, &exp.Start, &exp.End,
		&costModel, &config, &exp.DataVersion, &exp.CodeVersion, &exp.ContractVersion,
		&result, &exp.CreatedAt,
	)
	if errors.Is(err, pgx.ErrNoRows) {
		return experiment.Experiment{}, fmt.Errorf("%w: %s", experiment.ErrNotFound, id)
	}
	if err != nil {
		return experiment.Experiment{}, fmt.Errorf("load experiment %s: %w", id, err)
	}

	exp.Parameters = unmarshalObject(parameters)
	exp.CostModel = unmarshalObject(costModel)
	exp.BacktestConfig = unmarshalObject(config)
	exp.Result = json.RawMessage(result)
	return exp, nil
}

func (r *ExperimentRepository) List(ctx context.Context, filter experiment.ListFilter) ([]experiment.Summary, error) {
	// The metric projection is done in SQL so the journal does not transfer
	// every full result payload (curves and trade lists) just to render a table.
	query := strings.Builder{}
	query.WriteString(`
SELECT id, COALESCE(name, ''), strategy, symbol, interval,
       to_char(start_date, 'YYYY-MM-DD'), to_char(end_date, 'YYYY-MM-DD'),
       data_version, created_at,
       result #> '{metrics}' AS metrics
FROM experiments WHERE 1 = 1`)

	args := []any{}
	if filter.Strategy != "" {
		args = append(args, filter.Strategy)
		fmt.Fprintf(&query, " AND strategy = $%d", len(args))
	}
	if filter.Symbol != "" {
		args = append(args, filter.Symbol)
		fmt.Fprintf(&query, " AND symbol = $%d", len(args))
	}
	args = append(args, filter.Limit)
	fmt.Fprintf(&query, " ORDER BY created_at DESC LIMIT $%d", len(args))

	rows, err := r.pool.Query(ctx, query.String(), args...)
	if err != nil {
		return nil, fmt.Errorf("list experiments: %w", err)
	}
	defer rows.Close()

	summaries := make([]experiment.Summary, 0, filter.Limit)
	for rows.Next() {
		var (
			summary experiment.Summary
			metrics []byte
		)
		if err := rows.Scan(
			&summary.ID, &summary.Name, &summary.Strategy, &summary.Symbol, &summary.Interval,
			&summary.Start, &summary.End, &summary.DataVersion, &summary.CreatedAt, &metrics,
		); err != nil {
			return nil, fmt.Errorf("scan experiment summary: %w", err)
		}
		if len(metrics) > 0 {
			// A metric that could not be computed stays null in the summary
			// rather than becoming 0 — the journal must not show a fabricated
			// zero where the engine reported "not applicable".
			_ = json.Unmarshal(metrics, &summary.Metrics)
		}
		summaries = append(summaries, summary)
	}
	if err := rows.Err(); err != nil {
		return nil, fmt.Errorf("iterate experiments: %w", err)
	}
	return summaries, nil
}

func (r *ExperimentRepository) Delete(ctx context.Context, id string) error {
	tag, err := r.pool.Exec(ctx, `DELETE FROM experiments WHERE id = $1`, id)
	if err != nil {
		return fmt.Errorf("delete experiment %s: %w", id, err)
	}
	if tag.RowsAffected() == 0 {
		return fmt.Errorf("%w: %s", experiment.ErrNotFound, id)
	}
	return nil
}

func marshalObject(value map[string]any) ([]byte, error) {
	if value == nil {
		return []byte("{}"), nil
	}
	return json.Marshal(value)
}

func unmarshalObject(raw []byte) map[string]any {
	if len(raw) == 0 {
		return map[string]any{}
	}
	var value map[string]any
	if err := json.Unmarshal(raw, &value); err != nil {
		return map[string]any{}
	}
	return value
}

func nullIfEmpty(value string) any {
	if strings.TrimSpace(value) == "" {
		return nil
	}
	return value
}

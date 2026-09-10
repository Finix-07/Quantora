package postgres

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"strings"

	"github.com/jackc/pgx/v5"
	"github.com/jackc/pgx/v5/pgxpool"

	"github.com/anubhavjha/ai-quant-terminal/apps/api/internal/portfolio"
)

// PortfolioRepository is the PostgreSQL implementation of
// portfolio.Repository.
//
// Domain types cross the boundary in both directions: no pgx row, SQL fragment
// or driver type escapes this file (architecture.md §15).
type PortfolioRepository struct {
	pool *pgxpool.Pool
}

func NewPortfolioRepository(pool *pgxpool.Pool) *PortfolioRepository {
	return &PortfolioRepository{pool: pool}
}

// SavePortfolio writes the portfolio and replaces its positions.
//
// One transaction, because a portfolio whose header was updated but whose
// positions were not is a portfolio nobody asked for: a risk report run against
// it would describe holdings the user never had.
func (r *PortfolioRepository) SavePortfolio(ctx context.Context, p portfolio.Portfolio) error {
	tx, err := r.pool.Begin(ctx)
	if err != nil {
		return fmt.Errorf("begin transaction for portfolio %s: %w", p.ID, err)
	}
	defer func() { _ = tx.Rollback(ctx) }()

	_, err = tx.Exec(ctx, `
INSERT INTO portfolios (id, name, description, base_currency, benchmark, created_at, updated_at)
VALUES ($1, $2, $3, $4, $5, $6, $7)
ON CONFLICT (id) DO UPDATE SET
    name = EXCLUDED.name,
    description = EXCLUDED.description,
    base_currency = EXCLUDED.base_currency,
    benchmark = EXCLUDED.benchmark,
    updated_at = EXCLUDED.updated_at`,
		p.ID, p.Name, nullIfEmpty(p.Description), p.BaseCurrency, p.Benchmark,
		p.CreatedAt, p.UpdatedAt)
	if err != nil {
		return fmt.Errorf("upsert portfolio %s: %w", p.ID, err)
	}

	// Replace rather than merge. A holding the user removed must disappear; an
	// update that only ever added would leave a sold position quietly
	// contributing to every future risk report.
	if _, err := tx.Exec(ctx, `DELETE FROM positions WHERE portfolio_id = $1`, p.ID); err != nil {
		return fmt.Errorf("clear positions for %s: %w", p.ID, err)
	}
	for _, holding := range p.Holdings {
		_, err := tx.Exec(ctx, `
INSERT INTO positions (portfolio_id, symbol, quantity, cost_basis) VALUES ($1, $2, $3, $4)`,
			p.ID, holding.Symbol, holding.Quantity, holding.CostBasis)
		if err != nil {
			return fmt.Errorf("insert position %s for %s: %w", holding.Symbol, p.ID, err)
		}
	}

	if err := tx.Commit(ctx); err != nil {
		return fmt.Errorf("commit portfolio %s: %w", p.ID, err)
	}
	return nil
}

func (r *PortfolioRepository) GetPortfolio(ctx context.Context, id string) (portfolio.Portfolio, error) {
	var p portfolio.Portfolio
	err := r.pool.QueryRow(ctx, `
SELECT id, name, COALESCE(description, ''), base_currency, benchmark, created_at, updated_at
FROM portfolios WHERE id = $1`, id).
		Scan(&p.ID, &p.Name, &p.Description, &p.BaseCurrency, &p.Benchmark, &p.CreatedAt, &p.UpdatedAt)
	if errors.Is(err, pgx.ErrNoRows) {
		return portfolio.Portfolio{}, fmt.Errorf("%w: %s", portfolio.ErrNotFound, id)
	}
	if err != nil {
		return portfolio.Portfolio{}, fmt.Errorf("load portfolio %s: %w", id, err)
	}

	rows, err := r.pool.Query(ctx, `
SELECT symbol, quantity, cost_basis FROM positions WHERE portfolio_id = $1 ORDER BY symbol`, id)
	if err != nil {
		return portfolio.Portfolio{}, fmt.Errorf("load positions for %s: %w", id, err)
	}
	defer rows.Close()

	// Non-nil so an empty portfolio marshals as [] rather than null.
	p.Holdings = []portfolio.Holding{}
	for rows.Next() {
		var holding portfolio.Holding
		if err := rows.Scan(&holding.Symbol, &holding.Quantity, &holding.CostBasis); err != nil {
			return portfolio.Portfolio{}, fmt.Errorf("scan position: %w", err)
		}
		p.Holdings = append(p.Holdings, holding)
	}
	if err := rows.Err(); err != nil {
		return portfolio.Portfolio{}, fmt.Errorf("iterate positions for %s: %w", id, err)
	}
	return p, nil
}

func (r *PortfolioRepository) ListPortfolios(ctx context.Context, limit int) ([]portfolio.Summary, error) {
	// The holding count is aggregated in SQL so listing does not fetch every
	// position just to render a number.
	rows, err := r.pool.Query(ctx, `
SELECT p.id, p.name, COALESCE(p.description, ''), p.base_currency, p.benchmark,
       COUNT(pos.id), p.created_at, p.updated_at
FROM portfolios p
LEFT JOIN positions pos ON pos.portfolio_id = p.id
GROUP BY p.id
ORDER BY p.created_at DESC
LIMIT $1`, limit)
	if err != nil {
		return nil, fmt.Errorf("list portfolios: %w", err)
	}
	defer rows.Close()

	summaries := make([]portfolio.Summary, 0, limit)
	for rows.Next() {
		var s portfolio.Summary
		if err := rows.Scan(&s.ID, &s.Name, &s.Description, &s.BaseCurrency, &s.Benchmark,
			&s.HoldingCount, &s.CreatedAt, &s.UpdatedAt); err != nil {
			return nil, fmt.Errorf("scan portfolio summary: %w", err)
		}
		summaries = append(summaries, s)
	}
	return summaries, rows.Err()
}

func (r *PortfolioRepository) DeletePortfolio(ctx context.Context, id string) error {
	// Positions and reports cascade from the schema, so a delete cannot leave a
	// saved report pointing at a portfolio that no longer exists.
	tag, err := r.pool.Exec(ctx, `DELETE FROM portfolios WHERE id = $1`, id)
	if err != nil {
		return fmt.Errorf("delete portfolio %s: %w", id, err)
	}
	if tag.RowsAffected() == 0 {
		return fmt.Errorf("%w: %s", portfolio.ErrNotFound, id)
	}
	return nil
}

func (r *PortfolioRepository) SaveReport(ctx context.Context, report portfolio.Report) error {
	_, err := r.pool.Exec(ctx, `
INSERT INTO risk_reports (
    id, portfolio_id, name, kind, benchmark, interval, start_date, end_date, as_of_date,
    total_value, annualized_return, annualized_volatility, beta, sharpe, max_drawdown,
    report, created_at
) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14, $15, $16, $17)`,
		report.ID,
		nullIfEmpty(report.PortfolioID),
		nullIfEmpty(report.Name),
		report.Kind,
		report.Benchmark,
		report.Interval,
		report.Start,
		report.End,
		nullIfEmpty(report.AsOf),
		report.Metrics.TotalValue,
		report.Metrics.AnnualizedReturn,
		report.Metrics.AnnualizedVolatility,
		report.Metrics.Beta,
		report.Metrics.Sharpe,
		report.Metrics.MaxDrawdown,
		[]byte(report.Payload),
		report.CreatedAt,
	)
	if err != nil {
		return fmt.Errorf("insert risk report %s: %w", report.ID, err)
	}
	return nil
}

func (r *PortfolioRepository) GetReport(ctx context.Context, id string) (portfolio.Report, error) {
	var (
		report  portfolio.Report
		payload []byte
	)
	err := r.pool.QueryRow(ctx, `
SELECT id, COALESCE(portfolio_id, ''), COALESCE(name, ''), kind, benchmark, interval,
       to_char(start_date, 'YYYY-MM-DD'), to_char(end_date, 'YYYY-MM-DD'),
       COALESCE(to_char(as_of_date, 'YYYY-MM-DD'), ''),
       total_value, annualized_return, annualized_volatility, beta, sharpe, max_drawdown,
       report, created_at
FROM risk_reports WHERE id = $1`, id).
		Scan(&report.ID, &report.PortfolioID, &report.Name, &report.Kind, &report.Benchmark,
			&report.Interval, &report.Start, &report.End, &report.AsOf,
			&report.Metrics.TotalValue, &report.Metrics.AnnualizedReturn,
			&report.Metrics.AnnualizedVolatility, &report.Metrics.Beta,
			&report.Metrics.Sharpe, &report.Metrics.MaxDrawdown,
			&payload, &report.CreatedAt)
	if errors.Is(err, pgx.ErrNoRows) {
		return portfolio.Report{}, fmt.Errorf("%w: %s", portfolio.ErrNotFound, id)
	}
	if err != nil {
		return portfolio.Report{}, fmt.Errorf("load risk report %s: %w", id, err)
	}
	report.Payload = json.RawMessage(payload)
	return report, nil
}

func (r *PortfolioRepository) ListReports(ctx context.Context, filter portfolio.ReportFilter) ([]portfolio.ReportSummary, error) {
	query := strings.Builder{}
	query.WriteString(`
SELECT id, COALESCE(portfolio_id, ''), COALESCE(name, ''), kind, benchmark, interval,
       to_char(start_date, 'YYYY-MM-DD'), to_char(end_date, 'YYYY-MM-DD'),
       COALESCE(to_char(as_of_date, 'YYYY-MM-DD'), ''),
       total_value, annualized_return, annualized_volatility, beta, sharpe, max_drawdown,
       created_at
FROM risk_reports WHERE 1 = 1`)

	args := []any{}
	if filter.PortfolioID != "" {
		args = append(args, filter.PortfolioID)
		fmt.Fprintf(&query, " AND portfolio_id = $%d", len(args))
	}
	if filter.Kind != "" {
		args = append(args, filter.Kind)
		fmt.Fprintf(&query, " AND kind = $%d", len(args))
	}
	args = append(args, filter.Limit)
	fmt.Fprintf(&query, " ORDER BY created_at DESC LIMIT $%d", len(args))

	rows, err := r.pool.Query(ctx, query.String(), args...)
	if err != nil {
		return nil, fmt.Errorf("list risk reports: %w", err)
	}
	defer rows.Close()

	summaries := make([]portfolio.ReportSummary, 0, filter.Limit)
	for rows.Next() {
		var s portfolio.ReportSummary
		// The metric columns are nullable on purpose: a metric the engine could
		// not compute stays nil here and renders as null, never as a zero the
		// user would read as a measurement.
		if err := rows.Scan(&s.ID, &s.PortfolioID, &s.Name, &s.Kind, &s.Benchmark, &s.Interval,
			&s.Start, &s.End, &s.AsOf,
			&s.Metrics.TotalValue, &s.Metrics.AnnualizedReturn, &s.Metrics.AnnualizedVolatility,
			&s.Metrics.Beta, &s.Metrics.Sharpe, &s.Metrics.MaxDrawdown,
			&s.CreatedAt); err != nil {
			return nil, fmt.Errorf("scan risk report summary: %w", err)
		}
		summaries = append(summaries, s)
	}
	return summaries, rows.Err()
}

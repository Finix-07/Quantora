// Package postgres owns the API's PostgreSQL connection and the startup
// preconditions that must hold before the service accepts traffic.
//
// Repositories in sibling packages depend on the *pgxpool.Pool exposed here.
// Domain models never embed database types (architecture.md §15): the mapping
// happens inside each repository.
package postgres

import (
	"context"
	"errors"
	"fmt"
	"time"

	"github.com/jackc/pgx/v5/pgxpool"
)

// ErrMigrationsNotApplied means golang-migrate has never run against this
// database. Distinguishing it from a connection error matters: the operator
// fixes it with `make migrate`, not by debugging networking.
var ErrMigrationsNotApplied = errors.New("database migrations have not been applied")

// ErrMigrationsDirty means a migration failed part-way. Recovering by guessing
// is how schemas get silently corrupted, so the API refuses to start and points
// at the runbook (maintenance-operations.md §4).
var ErrMigrationsDirty = errors.New("database migrations are in a dirty state")

// Connect opens a pooled connection, retrying until the database is reachable
// or the deadline passes.
//
// The retry exists because Docker Compose starts `api` and `db` concurrently;
// without it the API would crash-loop for the few seconds Postgres takes to
// accept connections, which looks like a real failure in the logs. The retry
// window is bounded so a genuinely missing database still fails loudly.
func Connect(ctx context.Context, dsn string, waitFor time.Duration) (*pgxpool.Pool, error) {
	cfg, err := pgxpool.ParseConfig(dsn)
	if err != nil {
		return nil, fmt.Errorf("invalid DATABASE_URL: %w", err)
	}
	cfg.MaxConns = 10
	cfg.MaxConnLifetime = time.Hour
	cfg.HealthCheckPeriod = 30 * time.Second

	pool, err := pgxpool.NewWithConfig(ctx, cfg)
	if err != nil {
		return nil, fmt.Errorf("create connection pool: %w", err)
	}

	deadline := time.Now().Add(waitFor)
	var lastErr error
	for {
		pingCtx, cancel := context.WithTimeout(ctx, 3*time.Second)
		lastErr = pool.Ping(pingCtx)
		cancel()
		if lastErr == nil {
			return pool, nil
		}
		if time.Now().After(deadline) || ctx.Err() != nil {
			pool.Close()
			return nil, fmt.Errorf("database unreachable after %s: %w", waitFor, lastErr)
		}
		select {
		case <-ctx.Done():
			pool.Close()
			return nil, ctx.Err()
		case <-time.After(500 * time.Millisecond):
		}
	}
}

// MigrationState is golang-migrate's bookkeeping, read directly rather than
// through the migrate library so the API keeps no dependency on the migration
// tool it deliberately does not run (implementation-plan M1.5).
type MigrationState struct {
	Version int64
	Dirty   bool
}

// AssertMigrationsApplied verifies the schema is present and clean.
//
// This is a startup precondition, not a health check: an API that serves
// requests against an unmigrated database returns confusing 500s instead of
// saying what is actually wrong.
func AssertMigrationsApplied(ctx context.Context, pool *pgxpool.Pool) (MigrationState, error) {
	var exists bool
	err := pool.QueryRow(ctx, `SELECT to_regclass('public.schema_migrations') IS NOT NULL`).Scan(&exists)
	if err != nil {
		return MigrationState{}, fmt.Errorf("check schema_migrations: %w", err)
	}
	if !exists {
		return MigrationState{}, fmt.Errorf("%w: run `make migrate`", ErrMigrationsNotApplied)
	}

	var state MigrationState
	err = pool.QueryRow(ctx, `SELECT version, dirty FROM schema_migrations LIMIT 1`).
		Scan(&state.Version, &state.Dirty)
	if err != nil {
		// The table exists but holds no row: golang-migrate created it and then
		// failed, or someone truncated it. Either way the schema is unknown.
		return MigrationState{}, fmt.Errorf("%w: schema_migrations is empty (%v); run `make migrate`",
			ErrMigrationsNotApplied, err)
	}
	if state.Dirty {
		return state, fmt.Errorf(
			"%w at version %d: a migration failed part-way. Inspect db/migrations/ and the Postgres logs; do not force or skip it",
			ErrMigrationsDirty, state.Version)
	}
	return state, nil
}

// HealthCheck returns a probe suitable for the /healthz registry.
func HealthCheck(pool *pgxpool.Pool) func(context.Context) error {
	return func(ctx context.Context) error {
		if err := pool.Ping(ctx); err != nil {
			return fmt.Errorf("postgres unreachable: %w", err)
		}
		return nil
	}
}

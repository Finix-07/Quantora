package postgres

import (
	"context"
	"errors"
	"fmt"
	"os"
	"strings"
	"testing"
	"time"

	"github.com/jackc/pgx/v5/pgxpool"
)

// testPool connects to the real PostgreSQL instance named by TEST_DATABASE_URL.
//
// testing.md §2.3 requires repository behaviour to be verified against a real
// database rather than a mock, because golang-migrate's bookkeeping and the
// queries themselves are part of what is being tested. Without the variable the
// suite skips, so `go test ./...` stays runnable with no stack up.
func testPool(t *testing.T) *pgxpool.Pool {
	t.Helper()
	dsn := os.Getenv("TEST_DATABASE_URL")
	if dsn == "" {
		t.Skip("TEST_DATABASE_URL not set; start the stack and re-run to exercise the database path")
	}
	ctx, cancel := context.WithTimeout(context.Background(), 15*time.Second)
	defer cancel()

	pool, err := Connect(ctx, dsn, 10*time.Second)
	if err != nil {
		t.Fatalf("connect to test database: %v", err)
	}
	t.Cleanup(pool.Close)
	return pool
}

func TestConnectRejectsInvalidDSN(t *testing.T) {
	_, err := Connect(context.Background(), "not-a-dsn", time.Second)
	if err == nil {
		t.Fatal("expected an invalid DSN to be rejected")
	}
}

func TestConnectFailsLoudlyWhenUnreachable(t *testing.T) {
	// Port 1 is reserved and never accepts connections, so this exercises the
	// bounded-retry path without depending on the environment.
	start := time.Now()
	_, err := Connect(context.Background(), "postgres://quant:pw@127.0.0.1:1/quant?sslmode=disable", 1*time.Second)
	if err == nil {
		t.Fatal("expected an unreachable database to fail")
	}
	if elapsed := time.Since(start); elapsed > 10*time.Second {
		t.Errorf("retry window was not bounded: took %s", elapsed)
	}
}

func TestAssertMigrationsAppliedOnMigratedDatabase(t *testing.T) {
	pool := testPool(t)
	ctx := context.Background()

	state, err := AssertMigrationsApplied(ctx, pool)
	if err != nil {
		t.Fatalf("AssertMigrationsApplied on a migrated database: %v (did you run `make migrate`?)", err)
	}
	if state.Version < 1 {
		t.Errorf("schema version = %d, want at least the baseline migration (1)", state.Version)
	}
	if state.Dirty {
		t.Error("migrations reported dirty on a freshly migrated database")
	}
}

// withScratchDatabase creates an empty database so the "migrations missing" and
// "migrations dirty" preconditions can be observed for real rather than mocked.
func withScratchDatabase(t *testing.T, admin *pgxpool.Pool) *pgxpool.Pool {
	t.Helper()
	ctx := context.Background()
	name := fmt.Sprintf("aiqt_test_%d", time.Now().UnixNano())

	if _, err := admin.Exec(ctx, fmt.Sprintf("CREATE DATABASE %s", name)); err != nil {
		t.Fatalf("create scratch database: %v", err)
	}
	t.Cleanup(func() {
		_, _ = admin.Exec(context.Background(),
			fmt.Sprintf("DROP DATABASE IF EXISTS %s WITH (FORCE)", name))
	})

	cfg := admin.Config().ConnConfig.Copy()
	cfg.Database = name
	dsn := fmt.Sprintf("postgres://%s:%s@%s:%d/%s?sslmode=disable",
		cfg.User, cfg.Password, cfg.Host, cfg.Port, name)

	pool, err := Connect(ctx, dsn, 10*time.Second)
	if err != nil {
		t.Fatalf("connect to scratch database: %v", err)
	}
	t.Cleanup(pool.Close)
	return pool
}

func TestAssertMigrationsAppliedDetectsUnmigratedDatabase(t *testing.T) {
	admin := testPool(t)
	scratch := withScratchDatabase(t, admin)

	_, err := AssertMigrationsApplied(context.Background(), scratch)
	if !errors.Is(err, ErrMigrationsNotApplied) {
		t.Fatalf("error = %v, want ErrMigrationsNotApplied", err)
	}
	// The operator needs to be told the fix, not just the symptom.
	if got := err.Error(); !strings.Contains(got, "make migrate") {
		t.Errorf("error %q should name the command that fixes it", got)
	}
}

func TestAssertMigrationsAppliedDetectsDirtyState(t *testing.T) {
	admin := testPool(t)
	scratch := withScratchDatabase(t, admin)
	ctx := context.Background()

	// Reproduce exactly what golang-migrate leaves behind when a migration
	// fails part-way.
	_, err := scratch.Exec(ctx, `
		CREATE TABLE schema_migrations (version bigint NOT NULL PRIMARY KEY, dirty boolean NOT NULL);
		INSERT INTO schema_migrations (version, dirty) VALUES (3, true);`)
	if err != nil {
		t.Fatalf("seed dirty migration state: %v", err)
	}

	state, err := AssertMigrationsApplied(ctx, scratch)
	if !errors.Is(err, ErrMigrationsDirty) {
		t.Fatalf("error = %v, want ErrMigrationsDirty", err)
	}
	if state.Version != 3 {
		t.Errorf("version = %d, want the failing version 3 to be reported", state.Version)
	}
	if got := err.Error(); !strings.Contains(got, "do not force or skip it") {
		t.Errorf("error %q should carry the runbook's guidance", got)
	}
}

func TestAssertMigrationsAppliedDetectsEmptyBookkeepingTable(t *testing.T) {
	admin := testPool(t)
	scratch := withScratchDatabase(t, admin)
	ctx := context.Background()

	_, err := scratch.Exec(ctx,
		`CREATE TABLE schema_migrations (version bigint NOT NULL PRIMARY KEY, dirty boolean NOT NULL)`)
	if err != nil {
		t.Fatalf("seed empty bookkeeping table: %v", err)
	}

	if _, err := AssertMigrationsApplied(ctx, scratch); !errors.Is(err, ErrMigrationsNotApplied) {
		t.Fatalf("error = %v, want ErrMigrationsNotApplied for an empty schema_migrations", err)
	}
}

func TestHealthCheckReportsReachability(t *testing.T) {
	pool := testPool(t)
	if err := HealthCheck(pool)(context.Background()); err != nil {
		t.Fatalf("health check on a reachable database: %v", err)
	}
}

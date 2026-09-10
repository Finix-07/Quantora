// Command api is the AI Quant Terminal's Go application API.
//
// It owns HTTP, request orchestration, experiment lifecycle and persistence.
// It contains no quantitative formulas: every calculation is delegated to the
// Python quant-mcp service over HTTP/JSON (architecture.md §3.2, §13).
package main

import (
	"context"
	"errors"
	"flag"
	"fmt"
	"net/http"
	"os"
	"os/signal"
	"strconv"
	"syscall"
	"time"

	"github.com/anubhavjha/ai-quant-terminal/apps/api/internal/backtest"
	"github.com/anubhavjha/ai-quant-terminal/apps/api/internal/config"
	"github.com/anubhavjha/ai-quant-terminal/apps/api/internal/experiment"
	"github.com/anubhavjha/ai-quant-terminal/apps/api/internal/httpapi"
	"github.com/anubhavjha/ai-quant-terminal/apps/api/internal/logging"
	"github.com/anubhavjha/ai-quant-terminal/apps/api/internal/portfolio"
	"github.com/anubhavjha/ai-quant-terminal/apps/api/internal/quant"
	"github.com/anubhavjha/ai-quant-terminal/apps/api/internal/storage/postgres"
)

// healthcheckFlag lets the container's healthcheck probe /healthz by
// re-executing this same binary. The runtime image is distroless, so there is
// no curl or wget to call — and adding a shell to the image purely for health
// probing would be a worse trade than a 20-line self-probe.
var healthcheckFlag = flag.Bool("healthcheck", false, "probe this service's own /healthz and exit 0 (healthy) or 1")

func main() {
	flag.Parse()

	if *healthcheckFlag {
		os.Exit(selfHealthcheck())
	}

	if err := run(); err != nil {
		// Configuration and startup failures are fatal and loud: a service
		// that starts with a broken DSN and only fails on the first real
		// request is exactly the "plausible but incorrect" behaviour NFR5.6
		// forbids.
		fmt.Fprintf(os.Stderr, "api: fatal: %v\n", err)
		os.Exit(1)
	}
}

// selfHealthcheck performs the probe described on healthcheckFlag.
func selfHealthcheck() int {
	port := 8080
	if raw := os.Getenv("API_PORT"); raw != "" {
		if n, err := strconv.Atoi(raw); err == nil {
			port = n
		}
	}
	client := &http.Client{Timeout: 3 * time.Second}
	resp, err := client.Get(fmt.Sprintf("http://127.0.0.1:%d/healthz", port))
	if err != nil {
		fmt.Fprintf(os.Stderr, "healthcheck: %v\n", err)
		return 1
	}
	defer resp.Body.Close()
	if resp.StatusCode != http.StatusOK {
		fmt.Fprintf(os.Stderr, "healthcheck: /healthz returned %d\n", resp.StatusCode)
		return 1
	}
	return 0
}

func run() error {
	cfg, err := config.Load(".env", "../../.env", "/app/.env")
	if err != nil {
		return err
	}

	log := logging.New(cfg.LogLevel)
	log.Info("starting api",
		"version", httpapi.Version,
		"port", cfg.Port,
		"quant_mcp_url", cfg.QuantMCPURL,
	)

	startupCtx, cancelStartup := context.WithTimeout(context.Background(), 90*time.Second)
	defer cancelStartup()

	// Compose starts `api` and `db` together, so a short reachability wait is
	// normal rather than a failure. Past that window a missing database is a
	// fatal startup error, not something to discover on the first request.
	pool, err := postgres.Connect(startupCtx, cfg.DatabaseURL, 30*time.Second)
	if err != nil {
		return err
	}
	defer pool.Close()

	state, err := postgres.AssertMigrationsApplied(startupCtx, pool)
	if err != nil {
		return err
	}
	log.Info("database ready", "schema_version", state.Version)

	health := httpapi.NewHealthRegistry()
	// Probes are registered as their clients are wired in (quant-mcp at
	// M2.10). Until then /healthz reports only what it has actually verified.
	health.Register("process", func(context.Context) error { return nil })
	health.Register("database", postgres.HealthCheck(pool))

	quantClient := quant.New(cfg.QuantMCPURL, cfg.QuantMCPTimeout, log)
	health.Register("quant_mcp", quantClient.HealthCheck)

	backtestStore := backtest.NewMemoryStore(200)
	backtests := backtest.NewService(quantClient, backtestStore)
	experiments := experiment.NewService(
		postgres.NewExperimentRepository(pool), quantClient, backtestStore)
	portfolios := portfolio.NewService(postgres.NewPortfolioRepository(pool), quantClient)

	srv := &http.Server{
		Addr: fmt.Sprintf(":%d", cfg.Port),
		Handler: httpapi.NewRouter(httpapi.RouterDeps{
			Logger:      log,
			Health:      health,
			Backtests:   backtests,
			Experiments: experiments,
			Portfolios:  portfolios,
			Quant:       quantClient,
		}),
		ReadHeaderTimeout: 10 * time.Second,
		WriteTimeout:      cfg.RequestTimeout + 10*time.Second,
		IdleTimeout:       120 * time.Second,
	}

	ctx, stop := signal.NotifyContext(context.Background(), os.Interrupt, syscall.SIGTERM)
	defer stop()

	errCh := make(chan error, 1)
	go func() {
		if err := srv.ListenAndServe(); err != nil && !errors.Is(err, http.ErrServerClosed) {
			errCh <- fmt.Errorf("http server: %w", err)
		}
	}()

	select {
	case err := <-errCh:
		return err
	case <-ctx.Done():
		log.Info("shutdown signal received, draining connections")
	}

	shutdownCtx, cancel := context.WithTimeout(context.Background(), 20*time.Second)
	defer cancel()
	if err := srv.Shutdown(shutdownCtx); err != nil {
		return fmt.Errorf("graceful shutdown: %w", err)
	}
	log.Info("api stopped cleanly")
	return nil
}

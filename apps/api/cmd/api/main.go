// Command api is the AI Quant Terminal's Go application API.
//
// It owns HTTP, request orchestration, experiment lifecycle and persistence.
// It contains no quantitative formulas: every calculation is delegated to the
// Python quant-mcp service over HTTP/JSON (architecture.md §3.2, §13).
package main

import (
	"context"
	"errors"
	"fmt"
	"net/http"
	"os"
	"os/signal"
	"syscall"
	"time"

	"github.com/anubhavjha/ai-quant-terminal/apps/api/internal/config"
	"github.com/anubhavjha/ai-quant-terminal/apps/api/internal/httpapi"
	"github.com/anubhavjha/ai-quant-terminal/apps/api/internal/logging"
)

func main() {
	if err := run(); err != nil {
		// Configuration and startup failures are fatal and loud: a service
		// that starts with a broken DSN and only fails on the first real
		// request is exactly the "plausible but incorrect" behaviour NFR5.6
		// forbids.
		fmt.Fprintf(os.Stderr, "api: fatal: %v\n", err)
		os.Exit(1)
	}
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

	health := httpapi.NewHealthRegistry()
	// Dependency probes are registered as their clients are wired in
	// (database at M1.5, quant-mcp at M2.10). Until then /healthz honestly
	// reports only that the process itself is serving.
	health.Register("process", func(context.Context) error { return nil })

	srv := &http.Server{
		Addr:              fmt.Sprintf(":%d", cfg.Port),
		Handler:           httpapi.NewRouter(httpapi.RouterDeps{Logger: log, Health: health}),
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

package httpapi

import (
	"log/slog"
	"net/http"

	"github.com/anubhavjha/ai-quant-terminal/apps/api/internal/backtest"
	"github.com/anubhavjha/ai-quant-terminal/apps/api/internal/experiment"
	"github.com/anubhavjha/ai-quant-terminal/apps/api/internal/portfolio"
	"github.com/anubhavjha/ai-quant-terminal/apps/api/internal/quant"
)

// Version is the code version reported by /healthz and stamped onto saved
// experiments so a stored result can be traced to the code that produced it
// (architecture.md §9). Overridden at build time via -ldflags.
var Version = "dev"

// RouterDeps holds everything the HTTP layer needs. Handlers receive
// collaborators explicitly rather than reaching for globals, which keeps them
// testable and keeps business logic out of the HTTP layer.
type RouterDeps struct {
	Logger      *slog.Logger
	Health      *HealthRegistry
	Backtests   *backtest.Service
	Experiments *experiment.Service
	Portfolios  *portfolio.Service
	Quant       *quant.Client
}

// NewRouter builds the API's HTTP surface. Routes are registered here only;
// every handler delegates to an application service.
func NewRouter(deps RouterDeps) http.Handler {
	mux := http.NewServeMux()

	mux.HandleFunc("GET /healthz", HealthHandler(Version, deps.Health))

	if deps.Backtests != nil {
		handlers := BacktestHandlers{Service: deps.Backtests, Logger: deps.Logger}
		mux.HandleFunc("POST /api/backtests", handlers.Create)
		mux.HandleFunc("GET /api/backtests", handlers.List)
		mux.HandleFunc("GET /api/backtests/{id}", handlers.Get)
	}

	if deps.Experiments != nil {
		handlers := ExperimentHandlers{Service: deps.Experiments, Logger: deps.Logger}
		mux.HandleFunc("POST /api/experiments", handlers.Create)
		mux.HandleFunc("GET /api/experiments", handlers.List)
		mux.HandleFunc("GET /api/experiments/{id}", handlers.Get)
		mux.HandleFunc("DELETE /api/experiments/{id}", handlers.Delete)
		mux.HandleFunc("POST /api/experiments/{id}/rerun", handlers.Rerun)
	}

	if deps.Portfolios != nil {
		handlers := PortfolioHandlers{Service: deps.Portfolios, Logger: deps.Logger}
		mux.HandleFunc("POST /api/portfolio", handlers.Create)
		mux.HandleFunc("GET /api/portfolio", handlers.List)
		mux.HandleFunc("GET /api/portfolio/reports", handlers.ListReports)
		mux.HandleFunc("GET /api/portfolio/reports/{id}", handlers.GetReport)
		mux.HandleFunc("POST /api/portfolio/risk", handlers.Analyze)
		mux.HandleFunc("POST /api/portfolio/scenario", handlers.Scenario)
		mux.HandleFunc("GET /api/portfolio/{id}", handlers.Get)
		mux.HandleFunc("PUT /api/portfolio/{id}", handlers.Update)
		mux.HandleFunc("DELETE /api/portfolio/{id}", handlers.Delete)
	}

	if deps.Quant != nil {
		market := MarketHandlers{Client: deps.Quant, Logger: deps.Logger}
		mux.HandleFunc("GET /api/universe", market.Universe)
		mux.HandleFunc("GET /api/strategies", market.Strategies)
		mux.HandleFunc("GET /api/market/{symbol}", market.Prices)

		compare := CompareHandlers{Client: deps.Quant, Logger: deps.Logger}
		mux.HandleFunc("POST /api/strategies/compare", compare.Compare)
	}

	mux.HandleFunc("/", func(w http.ResponseWriter, r *http.Request) {
		WriteError(w, r, http.StatusNotFound, CodeNotFound,
			"No such endpoint: "+r.Method+" "+r.URL.Path, nil)
	})

	return Chain(mux,
		WithRequestID,
		WithRecovery(deps.Logger),
		WithAccessLog(deps.Logger),
	)
}

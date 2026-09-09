package httpapi

import (
	"log/slog"
	"net/http"
)

// Version is the code version reported by /healthz and stamped onto saved
// experiments so a stored result can be traced to the code that produced it
// (architecture.md §9). Overridden at build time via -ldflags.
var Version = "dev"

// RouterDeps holds everything the HTTP layer needs. Handlers receive
// collaborators explicitly rather than reaching for globals, which keeps them
// testable and keeps business logic out of the HTTP layer.
type RouterDeps struct {
	Logger *slog.Logger
	Health *HealthRegistry
}

// NewRouter builds the API's HTTP surface. Routes are registered here only;
// every handler delegates to an application service.
func NewRouter(deps RouterDeps) http.Handler {
	mux := http.NewServeMux()

	mux.HandleFunc("GET /healthz", HealthHandler(Version, deps.Health))

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

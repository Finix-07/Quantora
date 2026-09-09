package httpapi

import (
	"context"
	"net/http"
	"sort"
	"time"
)

// Check is one named dependency probe. It returns nil when the dependency is
// usable. Probes must be cheap and must not have side effects — in particular
// the LLM provider is never called from a health check, to avoid burning
// free-tier quota on health pings (maintenance-operations.md §3).
type Check func(ctx context.Context) error

// HealthRegistry collects the dependency probes reported by GET /healthz.
// Dependencies register themselves as they are wired in (database at M1.5,
// quant-mcp at M2.10), so the endpoint's meaning grows with the service
// instead of lying about what it verified.
type HealthRegistry struct {
	checks map[string]Check
}

func NewHealthRegistry() *HealthRegistry {
	return &HealthRegistry{checks: map[string]Check{}}
}

// Register adds a probe under a stable name. Re-registering a name replaces it.
func (h *HealthRegistry) Register(name string, c Check) {
	h.checks[name] = c
}

type dependencyStatus struct {
	Status string `json:"status"` // "ok" | "unhealthy"
	Error  string `json:"error,omitempty"`
}

// HealthResponse is deliberately explicit about *which* dependency failed;
// "unhealthy" alone would make the runbook (maintenance-operations.md §4)
// unusable.
type HealthResponse struct {
	Status       string                      `json:"status"` // "ok" | "degraded"
	Service      string                      `json:"service"`
	Version      string                      `json:"version"`
	Dependencies map[string]dependencyStatus `json:"dependencies"`
	CheckedAt    string                      `json:"checked_at"`
}

// HealthHandler runs every registered probe and returns 200 only when all of
// them pass. A failing dependency yields 503 so Docker Compose's healthcheck
// and the UI both see the service as not ready.
func HealthHandler(version string, reg *HealthRegistry) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		ctx, cancel := context.WithTimeout(r.Context(), 3*time.Second)
		defer cancel()

		resp := HealthResponse{
			Status:       "ok",
			Service:      "api",
			Version:      version,
			Dependencies: map[string]dependencyStatus{},
			CheckedAt:    time.Now().UTC().Format(time.RFC3339),
		}

		names := make([]string, 0, len(reg.checks))
		for name := range reg.checks {
			names = append(names, name)
		}
		sort.Strings(names)

		for _, name := range names {
			if err := reg.checks[name](ctx); err != nil {
				resp.Status = "degraded"
				resp.Dependencies[name] = dependencyStatus{Status: "unhealthy", Error: err.Error()}
				continue
			}
			resp.Dependencies[name] = dependencyStatus{Status: "ok"}
		}

		status := http.StatusOK
		if resp.Status != "ok" {
			status = http.StatusServiceUnavailable
		}
		WriteJSON(w, status, resp)
	}
}

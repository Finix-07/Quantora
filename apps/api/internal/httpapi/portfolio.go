package httpapi

import (
	"encoding/json"
	"errors"
	"log/slog"
	"net/http"
	"strconv"

	"github.com/anubhavjha/ai-quant-terminal/apps/api/internal/portfolio"
)

// PortfolioHandlers expose the portfolio and risk surface (FR7).
//
// They hold no portfolio mathematics: every number comes from the quant engine
// through the application service.
type PortfolioHandlers struct {
	Service *portfolio.Service
	Logger  *slog.Logger
}

func (h PortfolioHandlers) Create(w http.ResponseWriter, r *http.Request) {
	var req portfolio.SaveRequest
	if err := decodeJSON(w, r, &req); err != nil {
		WriteError(w, r, http.StatusBadRequest, CodeInvalidRequest, err.Error(), nil)
		return
	}
	saved, err := h.Service.Save(r.Context(), req)
	if err != nil {
		h.writeError(w, r, err)
		return
	}
	WriteJSON(w, http.StatusCreated, saved)
}

func (h PortfolioHandlers) Update(w http.ResponseWriter, r *http.Request) {
	var req portfolio.SaveRequest
	if err := decodeJSON(w, r, &req); err != nil {
		WriteError(w, r, http.StatusBadRequest, CodeInvalidRequest, err.Error(), nil)
		return
	}
	updated, err := h.Service.Update(r.Context(), r.PathValue("id"), req)
	if err != nil {
		h.writeError(w, r, err)
		return
	}
	WriteJSON(w, http.StatusOK, updated)
}

func (h PortfolioHandlers) List(w http.ResponseWriter, r *http.Request) {
	limit, err := intQuery(r, "limit")
	if err != nil {
		WriteError(w, r, http.StatusBadRequest, CodeInvalidRequest, err.Error(), nil)
		return
	}
	portfolios, err := h.Service.List(r.Context(), limit)
	if err != nil {
		h.writeError(w, r, err)
		return
	}
	WriteJSON(w, http.StatusOK, map[string]any{"portfolios": portfolios})
}

func (h PortfolioHandlers) Get(w http.ResponseWriter, r *http.Request) {
	p, err := h.Service.Get(r.Context(), r.PathValue("id"))
	if err != nil {
		h.writeError(w, r, err)
		return
	}
	WriteJSON(w, http.StatusOK, p)
}

func (h PortfolioHandlers) Delete(w http.ResponseWriter, r *http.Request) {
	if err := h.Service.Delete(r.Context(), r.PathValue("id")); err != nil {
		h.writeError(w, r, err)
		return
	}
	w.WriteHeader(http.StatusNoContent)
}

// Analyze computes risk metrics for a saved portfolio or an ad-hoc set.
func (h PortfolioHandlers) Analyze(w http.ResponseWriter, r *http.Request) {
	var req portfolio.AnalyzeRequest
	if err := decodeJSON(w, r, &req); err != nil {
		WriteError(w, r, http.StatusBadRequest, CodeInvalidRequest, err.Error(), nil)
		return
	}
	payload, saved, err := h.Service.Analyze(r.Context(), req)
	if err != nil {
		h.writeError(w, r, err)
		return
	}
	writeReport(w, payload, saved)
}

// Scenario reweights a portfolio and returns the before/after comparison.
func (h PortfolioHandlers) Scenario(w http.ResponseWriter, r *http.Request) {
	var req portfolio.ScenarioRequest
	if err := decodeJSON(w, r, &req); err != nil {
		WriteError(w, r, http.StatusBadRequest, CodeInvalidRequest, err.Error(), nil)
		return
	}
	payload, saved, err := h.Service.Scenario(r.Context(), req)
	if err != nil {
		h.writeError(w, r, err)
		return
	}
	writeReport(w, payload, saved)
}

func (h PortfolioHandlers) ListReports(w http.ResponseWriter, r *http.Request) {
	limit, err := intQuery(r, "limit")
	if err != nil {
		WriteError(w, r, http.StatusBadRequest, CodeInvalidRequest, err.Error(), nil)
		return
	}
	reports, err := h.Service.ListReports(r.Context(), portfolio.ReportFilter{
		PortfolioID: r.URL.Query().Get("portfolio_id"),
		Kind:        r.URL.Query().Get("kind"),
		Limit:       limit,
	})
	if err != nil {
		h.writeError(w, r, err)
		return
	}
	WriteJSON(w, http.StatusOK, map[string]any{"reports": reports})
}

func (h PortfolioHandlers) GetReport(w http.ResponseWriter, r *http.Request) {
	report, err := h.Service.GetReport(r.Context(), r.PathValue("id"))
	if err != nil {
		h.writeError(w, r, err)
		return
	}
	WriteJSON(w, http.StatusOK, report)
}

// writeReport returns the engine's report verbatim, with the saved report's
// identifiers alongside it when one was persisted.
//
// The engine's payload is not re-shaped: its structure is defined once, in
// Python, and rebuilding it here would be a second definition free to drift.
func writeReport(w http.ResponseWriter, payload json.RawMessage, saved *portfolio.Report) {
	if saved == nil {
		w.Header().Set("Content-Type", "application/json; charset=utf-8")
		w.WriteHeader(http.StatusOK)
		_, _ = w.Write(payload)
		return
	}
	WriteJSON(w, http.StatusOK, map[string]any{
		"report":       json.RawMessage(payload),
		"saved_report": map[string]any{"id": saved.ID, "kind": saved.Kind, "created_at": saved.CreatedAt},
	})
}

func (h PortfolioHandlers) writeError(w http.ResponseWriter, r *http.Request, err error) {
	switch {
	case errors.Is(err, portfolio.ErrNotFound):
		WriteError(w, r, http.StatusNotFound, CodeNotFound,
			"No portfolio or report with that ID.", nil)
		return
	case errors.Is(err, portfolio.ErrInvalid):
		WriteError(w, r, http.StatusBadRequest, CodeInvalidRequest, err.Error(), nil)
		return
	}
	// Anything else came from the quant engine; its own message is forwarded
	// rather than collapsed, because it is the part the user can act on.
	writeQuantError(w, r, h.Logger, err)
}

func intQuery(r *http.Request, name string) (int, error) {
	raw := r.URL.Query().Get(name)
	if raw == "" {
		return 0, nil
	}
	value, err := strconv.Atoi(raw)
	if err != nil {
		return 0, errors.New(name + " must be an integer")
	}
	return value, nil
}

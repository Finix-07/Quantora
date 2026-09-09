package httpapi

import (
	"errors"
	"log/slog"
	"net/http"
	"strconv"

	"github.com/anubhavjha/ai-quant-terminal/apps/api/internal/backtest"
	"github.com/anubhavjha/ai-quant-terminal/apps/api/internal/experiment"
)

// ExperimentHandlers expose the reproducible-unit lifecycle.
type ExperimentHandlers struct {
	Service *experiment.Service
	Logger  *slog.Logger
}

func (h ExperimentHandlers) Create(w http.ResponseWriter, r *http.Request) {
	var req experiment.SaveRequest
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

func (h ExperimentHandlers) List(w http.ResponseWriter, r *http.Request) {
	query := r.URL.Query()
	filter := experiment.ListFilter{
		Strategy: query.Get("strategy"),
		Symbol:   query.Get("symbol"),
	}
	if raw := query.Get("limit"); raw != "" {
		limit, err := strconv.Atoi(raw)
		if err != nil {
			WriteError(w, r, http.StatusBadRequest, CodeInvalidRequest, "limit must be an integer", nil)
			return
		}
		filter.Limit = limit
	}

	summaries, err := h.Service.List(r.Context(), filter)
	if err != nil {
		h.writeError(w, r, err)
		return
	}
	WriteJSON(w, http.StatusOK, map[string]any{"experiments": summaries})
}

func (h ExperimentHandlers) Get(w http.ResponseWriter, r *http.Request) {
	saved, err := h.Service.Get(r.Context(), r.PathValue("id"))
	if err != nil {
		h.writeError(w, r, err)
		return
	}
	WriteJSON(w, http.StatusOK, saved)
}

func (h ExperimentHandlers) Delete(w http.ResponseWriter, r *http.Request) {
	if err := h.Service.Delete(r.Context(), r.PathValue("id")); err != nil {
		h.writeError(w, r, err)
		return
	}
	w.WriteHeader(http.StatusNoContent)
}

// Rerun re-executes a saved experiment. The caller supplies only the ID —
// having to re-enter the configuration is exactly what FR5 forbids.
func (h ExperimentHandlers) Rerun(w http.ResponseWriter, r *http.Request) {
	outcome, err := h.Service.Rerun(r.Context(), r.PathValue("id"))
	if err != nil {
		h.writeError(w, r, err)
		return
	}
	// 200 even when the rerun did not reproduce: the rerun itself succeeded,
	// and the verdict is the answer to the user's question, not an error.
	WriteJSON(w, http.StatusOK, outcome)
}

func (h ExperimentHandlers) writeError(w http.ResponseWriter, r *http.Request, err error) {
	switch {
	case errors.Is(err, experiment.ErrNotFound):
		WriteError(w, r, http.StatusNotFound, CodeNotFound, "No experiment with that ID.", nil)
		return
	case errors.Is(err, backtest.ErrNotFound):
		// Saving by backtest_id can reference a run that has since been evicted
		// from the in-memory store. That is the user's situation to understand,
		// not an internal failure.
		WriteError(w, r, http.StatusNotFound, CodeNotFound,
			"That backtest is no longer available. Results are held in memory until they are "+
				"saved as an experiment, so a restart clears them — re-run the backtest and save it.", nil)
		return
	case errors.Is(err, experiment.ErrInvalid):
		WriteError(w, r, http.StatusBadRequest, CodeInvalidRequest, err.Error(), nil)
		return
	}
	writeQuantError(w, r, h.Logger, err)
}

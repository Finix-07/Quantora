package httpapi

import (
	"encoding/json"
	"errors"
	"log/slog"
	"net/http"
	"strconv"

	"github.com/anubhavjha/ai-quant-terminal/apps/api/internal/backtest"
	"github.com/anubhavjha/ai-quant-terminal/apps/api/internal/logging"
	"github.com/anubhavjha/ai-quant-terminal/apps/api/internal/quant"
)

// maxRequestBody bounds an inbound JSON body. A backtest request is a few
// hundred bytes; anything near this limit is a mistake or an attack.
const maxRequestBody = 1 << 20

// BacktestHandlers renders the backtest endpoints. All logic lives in the
// application service; these functions decode, delegate and translate errors.
type BacktestHandlers struct {
	Service *backtest.Service
	Logger  *slog.Logger
}

func (h BacktestHandlers) Create(w http.ResponseWriter, r *http.Request) {
	var req backtest.Request
	if err := decodeJSON(w, r, &req); err != nil {
		WriteError(w, r, http.StatusBadRequest, CodeInvalidRequest, err.Error(), nil)
		return
	}

	record, err := h.Service.Run(r.Context(), req)
	if err != nil {
		h.writeServiceError(w, r, err)
		return
	}

	WriteJSON(w, http.StatusCreated, record)
}

func (h BacktestHandlers) Get(w http.ResponseWriter, r *http.Request) {
	id := r.PathValue("id")
	record, err := h.Service.Get(r.Context(), id)
	if err != nil {
		h.writeServiceError(w, r, err)
		return
	}
	WriteJSON(w, http.StatusOK, record)
}

func (h BacktestHandlers) List(w http.ResponseWriter, r *http.Request) {
	limit := 50
	if raw := r.URL.Query().Get("limit"); raw != "" {
		parsed, err := strconv.Atoi(raw)
		if err != nil {
			WriteError(w, r, http.StatusBadRequest, CodeInvalidRequest,
				"limit must be an integer", nil)
			return
		}
		limit = parsed
	}

	records, err := h.Service.List(r.Context(), limit)
	if err != nil {
		h.writeServiceError(w, r, err)
		return
	}
	WriteJSON(w, http.StatusOK, map[string]any{
		"backtests": records,
		// Stated rather than implied: until experiments land (M3.5) these
		// results live in the API process and disappear on restart. A user who
		// does not know that would think their work was lost silently.
		"persistence": "in_memory_until_experiments_are_implemented",
	})
}

// writeServiceError maps a domain error to an HTTP response.
//
// An error from quant-mcp is forwarded with its own code and message. That
// service already produced a specific, actionable explanation ("Strategy 'macd'
// needs at least 35 bars … Widen the date range"), and replacing it with a
// generic "upstream error" would discard the only part the user can act on.
func (h BacktestHandlers) writeServiceError(w http.ResponseWriter, r *http.Request, err error) {
	log := logging.FromContext(r.Context(), h.Logger)

	switch {
	case errors.Is(err, backtest.ErrNotFound):
		WriteError(w, r, http.StatusNotFound, CodeNotFound,
			"No backtest with that ID. Results are held in memory until experiment "+
				"persistence lands, so a restart clears them.", nil)
		return
	case errors.Is(err, backtest.ErrInvalidRequest):
		WriteError(w, r, http.StatusBadRequest, CodeInvalidRequest, err.Error(), nil)
		return
	case errors.Is(err, quant.ErrUnavailable):
		log.Error("quant service unreachable", "error", err)
		WriteError(w, r, http.StatusServiceUnavailable, CodeUpstreamFailure,
			"The quant service is unreachable, so no backtest could be run. "+
				"Check that the quant-mcp container is running (`docker compose ps`).", nil)
		return
	}

	var upstream *quant.UpstreamError
	if errors.As(err, &upstream) {
		status := upstream.StatusCode
		if status < 400 || status > 599 {
			status = http.StatusBadGateway
		}
		WriteError(w, r, status, ErrorCode(upstream.Code), upstream.Message, upstream.Details)
		return
	}

	log.Error("backtest failed", "error", err)
	WriteError(w, r, http.StatusInternalServerError, CodeInternal,
		"The backtest could not be completed because of an internal error.", nil)
}

func decodeJSON(w http.ResponseWriter, r *http.Request, target any) error {
	r.Body = http.MaxBytesReader(w, r.Body, maxRequestBody)
	decoder := json.NewDecoder(r.Body)
	// Unknown fields are an error rather than being ignored: a typo'd
	// "commision_bps" that silently applied the default would produce a result
	// with different cost assumptions than the user asked for (NFR5.2).
	decoder.DisallowUnknownFields()
	if err := decoder.Decode(target); err != nil {
		return errors.New("could not read the request body as JSON: " + err.Error())
	}
	return nil
}

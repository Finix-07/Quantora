package httpapi

import (
	"encoding/json"
	"errors"
	"log/slog"
	"net/http"

	"github.com/anubhavjha/ai-quant-terminal/apps/api/internal/logging"
	"github.com/anubhavjha/ai-quant-terminal/apps/api/internal/quant"
)

// MarketHandlers expose read-only reference and market data.
//
// They pass the engine's payload through unchanged: re-shaping validated market
// data in Go would mean a second definition of the same contract, and the two
// would drift.
type MarketHandlers struct {
	Client *quant.Client
	Logger *slog.Logger
}

func (h MarketHandlers) Universe(w http.ResponseWriter, r *http.Request) {
	h.proxy(w, r, func() (json.RawMessage, error) { return h.Client.Universe(r.Context()) })
}

func (h MarketHandlers) Strategies(w http.ResponseWriter, r *http.Request) {
	h.proxy(w, r, func() (json.RawMessage, error) { return h.Client.Strategies(r.Context()) })
}

func (h MarketHandlers) Prices(w http.ResponseWriter, r *http.Request) {
	symbol := r.PathValue("symbol")
	query := r.URL.Query()
	start, end := query.Get("start"), query.Get("end")
	if start == "" || end == "" {
		WriteError(w, r, http.StatusBadRequest, CodeInvalidRequest,
			"start and end query parameters are required (ISO dates, YYYY-MM-DD).", nil)
		return
	}
	interval := query.Get("interval")

	h.proxy(w, r, func() (json.RawMessage, error) {
		return h.Client.MarketData(r.Context(), symbol, start, end, interval)
	})
}

func (h MarketHandlers) proxy(w http.ResponseWriter, r *http.Request, call func() (json.RawMessage, error)) {
	payload, err := call()
	if err != nil {
		writeQuantError(w, r, h.Logger, err)
		return
	}
	w.Header().Set("Content-Type", "application/json; charset=utf-8")
	w.WriteHeader(http.StatusOK)
	_, _ = w.Write(payload)
}

func writeQuantError(w http.ResponseWriter, r *http.Request, log *slog.Logger, err error) {
	if errors.Is(err, quant.ErrUnavailable) {
		logging.FromContext(r.Context(), log).Error("quant service unreachable", "error", err)
		WriteError(w, r, http.StatusServiceUnavailable, CodeUpstreamFailure,
			"The quant service is unreachable. Check that the quant-mcp container is "+
				"running (`docker compose ps`).", nil)
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

	logging.FromContext(r.Context(), log).Error("quant call failed", "error", err)
	WriteError(w, r, http.StatusInternalServerError, CodeInternal,
		"The request could not be completed because of an internal error.", nil)
}

package httpapi

import (
	"log/slog"
	"net/http"
	"strconv"
	"strings"
	"time"

	"github.com/anubhavjha/ai-quant-terminal/apps/api/internal/quant"
)

// CompareHandlers expose side-by-side strategy comparison (FR6).
type CompareHandlers struct {
	Client *quant.Client
	Logger *slog.Logger
}

// compareRequest is the API-level body. It mirrors the engine's shape but is
// declared here so the HTTP surface has its own validated contract rather than
// forwarding whatever arrived.
type compareRequest struct {
	Symbol         string               `json:"symbol"`
	Strategies     []quant.CompareEntry `json:"strategies"`
	Start          string               `json:"start"`
	End            string               `json:"end"`
	Interval       string               `json:"interval"`
	InitialCash    float64              `json:"initial_cash"`
	CostModel      *quant.CostModel     `json:"cost_model"`
	ExecutionModel string               `json:"execution_model"`
	AllowShort     bool                 `json:"allow_short"`
	LiquidateAtEnd *bool                `json:"liquidate_at_end"`
	RiskFreeRate   float64              `json:"risk_free_rate"`
}

func (r compareRequest) validate() []string {
	var problems []string
	if strings.TrimSpace(r.Symbol) == "" {
		problems = append(problems, "symbol is required")
	}
	if len(r.Strategies) < 2 {
		problems = append(problems,
			"a comparison needs at least two entries in `strategies`; use POST /api/backtests to run one")
	}
	for i, entry := range r.Strategies {
		if strings.TrimSpace(entry.Strategy) == "" {
			problems = append(problems,
				"strategies["+strconv.Itoa(i)+"] is missing a `strategy` name")
		}
	}
	if !isISODate(r.Start) || !isISODate(r.End) {
		problems = append(problems, "start and end must be ISO dates (YYYY-MM-DD)")
	} else if r.Start > r.End {
		problems = append(problems, "start must not be after end")
	}
	if r.InitialCash < 0 {
		problems = append(problems, "initial_cash must be positive")
	}
	return problems
}

func (h CompareHandlers) Compare(w http.ResponseWriter, r *http.Request) {
	var req compareRequest
	if err := decodeJSON(w, r, &req); err != nil {
		WriteError(w, r, http.StatusBadRequest, CodeInvalidRequest, err.Error(), nil)
		return
	}
	if problems := req.validate(); len(problems) > 0 {
		// Every problem at once, so the user fixes the request in one pass
		// rather than one field per round trip.
		WriteError(w, r, http.StatusBadRequest, CodeInvalidRequest,
			"The comparison request was not valid: "+strings.Join(problems, "; "), nil)
		return
	}

	payload, err := h.Client.CompareStrategies(r.Context(), quant.CompareRequest{
		Symbol:         req.Symbol,
		Strategies:     req.Strategies,
		Start:          req.Start,
		End:            req.End,
		Interval:       defaultTo(req.Interval, "1d"),
		InitialCash:    positiveOr(req.InitialCash, 1_000_000),
		CostModel:      req.CostModel,
		ExecutionModel: defaultTo(req.ExecutionModel, "next_bar_open"),
		AllowShort:     req.AllowShort,
		LiquidateAtEnd: req.LiquidateAtEnd == nil || *req.LiquidateAtEnd,
		RiskFreeRate:   req.RiskFreeRate,
	})
	if err != nil {
		writeQuantError(w, r, h.Logger, err)
		return
	}

	w.Header().Set("Content-Type", "application/json; charset=utf-8")
	w.WriteHeader(http.StatusOK)
	_, _ = w.Write(payload)
}

func isISODate(value string) bool {
	_, err := time.Parse(time.DateOnly, value)
	return err == nil
}

func defaultTo(value, fallback string) string {
	if strings.TrimSpace(value) == "" {
		return fallback
	}
	return value
}

func positiveOr(value, fallback float64) float64 {
	if value <= 0 {
		return fallback
	}
	return value
}

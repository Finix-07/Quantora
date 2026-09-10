package quant

import (
	"context"
	"encoding/json"
)

// CompareRequest mirrors quant-mcp's POST /v1/strategies/compare body.
//
// The strategy list travels in one request rather than as several backtests
// because every entry must run on byte-identical data. Two fetches of the same
// range can differ — yfinance revises history — and a comparison assembled from
// separate calls would attribute a data difference to a strategy difference.
type CompareRequest struct {
	Symbol         string         `json:"symbol"`
	Strategies     []CompareEntry `json:"strategies"`
	Start          string         `json:"start"`
	End            string         `json:"end"`
	Interval       string         `json:"interval,omitempty"`
	InitialCash    float64        `json:"initial_cash,omitempty"`
	CostModel      *CostModel     `json:"cost_model,omitempty"`
	ExecutionModel string         `json:"execution_model,omitempty"`
	AllowShort     bool           `json:"allow_short"`
	LiquidateAtEnd bool           `json:"liquidate_at_end"`
	RiskFreeRate   float64        `json:"risk_free_rate,omitempty"`
}

// CompareEntry is one strategy in a comparison.
type CompareEntry struct {
	Strategy   string         `json:"strategy"`
	Parameters map[string]any `json:"parameters,omitempty"`
	Label      string         `json:"label,omitempty"`
}

// CompareStrategies returns the comparison contract verbatim, for the same
// reason RunBacktest does: the shape is defined once, in Python.
func (c *Client) CompareStrategies(ctx context.Context, req CompareRequest) (json.RawMessage, error) {
	return c.postJSON(ctx, "/v1/strategies/compare", req)
}

package quant

import (
	"context"
	"encoding/json"
)

// Holding is one position sent to the portfolio engine.
//
// CostBasis is a pointer so "not recorded" survives the wire as null. A float64
// zero would arrive at the engine as "acquired for free" and come back with a
// fabricated unrealised gain attached to it.
type Holding struct {
	Symbol    string   `json:"symbol"`
	Quantity  float64  `json:"quantity"`
	CostBasis *float64 `json:"cost_basis,omitempty"`
}

// PortfolioRiskRequest mirrors quant-mcp's POST /v1/portfolio/risk body.
type PortfolioRiskRequest struct {
	Holdings     []Holding `json:"holdings"`
	Start        string    `json:"start"`
	End          string    `json:"end"`
	Interval     string    `json:"interval,omitempty"`
	Benchmark    string    `json:"benchmark,omitempty"`
	RiskFreeRate float64   `json:"risk_free_rate,omitempty"`
	Name         string    `json:"name,omitempty"`
	BaseCurrency string    `json:"base_currency,omitempty"`
}

// PortfolioScenarioRequest mirrors quant-mcp's POST /v1/portfolio/scenario body.
//
// The holdings and the reweighting travel in one request because both states
// must be measured over a single fetch of the same bars. Split into two calls,
// a provider revising history between them would show up as a risk difference
// the user would attribute to their reweighting.
type PortfolioScenarioRequest struct {
	PortfolioRiskRequest
	Weights map[string]float64 `json:"weights"`
}

// AnalyzePortfolioRisk returns the risk report verbatim.
//
// Raw JSON, for the same reason RunBacktest returns raw JSON: the report's shape
// is defined once, in Python, and a hand-maintained Go mirror would be a second
// definition free to drift. The handful of fields the API genuinely reasons
// about — the metrics it promotes into columns for the report list — are
// projected out separately by the portfolio service.
func (c *Client) AnalyzePortfolioRisk(ctx context.Context, req PortfolioRiskRequest) (json.RawMessage, error) {
	return c.postJSON(ctx, "/v1/portfolio/risk", req)
}

// RunPortfolioScenario returns the before/after comparison verbatim.
func (c *Client) RunPortfolioScenario(ctx context.Context, req PortfolioScenarioRequest) (json.RawMessage, error) {
	return c.postJSON(ctx, "/v1/portfolio/scenario", req)
}

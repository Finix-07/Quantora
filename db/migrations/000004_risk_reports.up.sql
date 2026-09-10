-- 000004_risk_reports
--
-- A saved risk report is a portfolio analysis frozen at the moment it was run:
-- the metrics, the allocation, the correlation matrix, the assumptions and the
-- data versions behind them (requirements.md FR7, NFR4). Re-running the same
-- portfolio next week gives different numbers because the market moved, so a
-- report only means something if the state it was computed from travels with it.
--
-- Schema notes worth stating:
--
--  * `report` is JSONB for the same reason `experiments.result` is: its shape is
--    owned by the Python engine (services/quant/portfolio/), and a normalised
--    mirror here would be a second definition free to drift from the first.
--    Scenario reports and plain risk reports share the column; `kind` says which
--    is inside, so a reader never has to guess from the payload's shape.
--
--  * The promoted metric columns exist only because the report list sorts and
--    filters on them. They are a *view* of `report`, not a rival source of
--    truth, and they are all nullable — NULL means the engine reported the
--    metric as unavailable, with the reason recorded inside `report.metrics
--    .unavailable`. It must never be read as zero: a beta of 0 says "this
--    portfolio does not move with the market", while NULL says "we could not
--    tell", and the whole engine is built on keeping those apart (NFR5.6).
--
--  * `portfolio_id` is nullable. Risk can be analysed on holdings posted
--    inline, without saving a portfolio first, and forcing a portfolio row to
--    exist would make the cheap question expensive.
--
--  * `as_of_date` is the last session common to every holding, which is not
--    necessarily `end_date`: the requested window's final days may be a
--    weekend, a holiday, or a session one instrument did not trade.

CREATE TABLE risk_reports (
    id                     TEXT        PRIMARY KEY,
    portfolio_id           TEXT        REFERENCES portfolios (id) ON DELETE CASCADE,
    name                   TEXT,

    -- 'risk' = a single-state analysis; 'scenario' = a before/after comparison.
    kind                   TEXT        NOT NULL DEFAULT 'risk',

    -- What was analysed, over what window, against what
    benchmark              TEXT        NOT NULL,
    interval               TEXT        NOT NULL DEFAULT '1d',
    start_date             DATE        NOT NULL,
    end_date               DATE        NOT NULL,
    as_of_date             DATE,

    -- Promoted for the list view only. NULL means "the engine could not compute
    -- this"; see the note above.
    total_value            DOUBLE PRECISION,
    annualized_return      DOUBLE PRECISION,
    annualized_volatility  DOUBLE PRECISION,
    beta                   DOUBLE PRECISION,
    sharpe                 DOUBLE PRECISION,
    max_drawdown           DOUBLE PRECISION,

    -- What came out, verbatim
    report                 JSONB       NOT NULL,

    created_at             TIMESTAMPTZ NOT NULL DEFAULT now(),

    CONSTRAINT risk_reports_date_range_valid CHECK (start_date <= end_date),
    CONSTRAINT risk_reports_kind_known CHECK (kind IN ('risk', 'scenario'))
);

-- The report list is ordered newest-first across all portfolios.
CREATE INDEX risk_reports_created_at_idx ON risk_reports (created_at DESC);

-- "Show me this portfolio's history of risk reports, newest first" is the
-- product's actual query: the value of saving a report is watching a portfolio's
-- risk change over time.
CREATE INDEX risk_reports_portfolio_idx ON risk_reports (portfolio_id, created_at DESC);

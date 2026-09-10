-- 000003_portfolios
--
-- A portfolio is a named set of holdings the user maintains and analyses
-- (requirements.md FR7). Unlike an experiment, it is *mutable*: positions are
-- added, resized and closed, and the risk report is derived from whatever the
-- holdings are at the moment it is run.
--
-- Schema notes worth stating:
--
--  * Positions are real rows, not a JSONB array on `portfolios`. The rest of
--    this schema keeps engine-shaped payloads as JSONB because their shapes are
--    owned by the Python engine, but a position is not an engine payload — it
--    is user input the API must be able to constrain and query. Rows are what
--    let the database enforce "one row per symbol per portfolio" and
--    "quantity > 0", and what make "which portfolios hold RELIANCE.NS?"
--    an index lookup rather than a scan-and-parse over every JSON document.
--
--  * `quantity` and `cost_basis` are NUMERIC, not DOUBLE PRECISION. They are
--    quantities of money and shares that a user typed in; binary floating point
--    would round 0.1 on the way in and hand back a different number than was
--    entered. Market *statistics* computed from them are floats, because they
--    are estimates rather than records.
--
--  * `cost_basis` is nullable and stays that way. A portfolio is useful for
--    risk analysis whether or not the user recorded what they paid, and a
--    default of 0 would turn "not recorded" into "acquired for free" and report
--    a fabricated unrealised gain (NFR5.6).
--
--  * `benchmark` lives on the portfolio because beta is only meaningful
--    relative to a stated benchmark, and a saved report must be able to say
--    which one it used months later (NFR4).

CREATE TABLE portfolios (
    id             TEXT        PRIMARY KEY,
    name           TEXT        NOT NULL,
    description    TEXT,

    base_currency  TEXT        NOT NULL DEFAULT 'INR',
    -- The instrument beta is measured against. Stored per portfolio rather than
    -- assumed globally: a bank-heavy book is more honestly measured against
    -- BANKNIFTY than against NIFTY.
    benchmark      TEXT        NOT NULL DEFAULT 'NIFTY',

    created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at     TIMESTAMPTZ NOT NULL DEFAULT now(),

    CONSTRAINT portfolios_name_not_blank CHECK (length(btrim(name)) > 0)
);

CREATE TABLE positions (
    id            BIGSERIAL      PRIMARY KEY,
    portfolio_id  TEXT           NOT NULL REFERENCES portfolios (id) ON DELETE CASCADE,
    symbol        TEXT           NOT NULL,

    quantity      NUMERIC(24, 8) NOT NULL,
    cost_basis    NUMERIC(24, 8),

    created_at    TIMESTAMPTZ    NOT NULL DEFAULT now(),

    -- Two rows for the same instrument almost always mean the user pasted the
    -- same line twice. Silently summing them would hide the mistake while
    -- changing every weight in the portfolio, so the database refuses.
    CONSTRAINT positions_one_row_per_symbol UNIQUE (portfolio_id, symbol),
    -- Short positions are not modelled by the portfolio engine: the scenario
    -- contract is a vector of non-negative weights that normalises to 1, and a
    -- negative weight has no single sensible normalisation. The constraint
    -- keeps the database from holding a portfolio the engine cannot analyse.
    CONSTRAINT positions_quantity_positive CHECK (quantity > 0),
    CONSTRAINT positions_cost_basis_non_negative CHECK (cost_basis IS NULL OR cost_basis >= 0)
);

-- Every risk run loads a portfolio's positions in one go; without this the
-- lookup scans the table. The unique constraint above already indexes
-- (portfolio_id, symbol), but this one supports the ordered read the API does.
CREATE INDEX positions_portfolio_idx ON positions (portfolio_id, symbol);

-- "Which portfolios hold this instrument?" is the question that makes positions
-- rows rather than a JSON blob; this is the index that answers it.
CREATE INDEX positions_symbol_idx ON positions (symbol);

-- The portfolio list is ordered newest-first.
CREATE INDEX portfolios_created_at_idx ON portfolios (created_at DESC);

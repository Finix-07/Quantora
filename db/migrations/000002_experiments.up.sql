-- 000002_experiments
--
-- An experiment is the reproducible unit of this product (requirements.md FR5,
-- NFR6): everything needed to rerun a backtest and get the same numbers,
-- persisted alongside the numbers themselves.
--
-- Schema notes worth stating:
--
--  * `parameters`, `cost_model`, `backtest_config` and `result` are JSONB rather
--    than columns. Their shapes are owned by the Python engine
--    (architecture.md §8) and a normalised mirror here would be a second
--    definition free to drift from the first. The columns that ARE promoted out
--    (strategy, symbol, dates, data_version) are the ones the API filters and
--    sorts on, and they are a *view* of the result, not a rival source of truth.
--
--  * `data_version` is a content hash of the bars the run used. yfinance revises
--    history, so "same config" is not enough to claim reproducibility — a rerun
--    compares this value and can tell the user the source data itself changed
--    rather than silently reporting different numbers (NFR6).
--
--  * `code_version` records which build produced the result, so a number can be
--    attributed to the code behind it (architecture.md §9).

CREATE TABLE experiments (
    id                TEXT        PRIMARY KEY,
    name              TEXT,
    notes             TEXT,

    -- What was run
    strategy          TEXT        NOT NULL,
    parameters        JSONB       NOT NULL DEFAULT '{}'::jsonb,
    symbol            TEXT        NOT NULL,
    universe          TEXT,
    interval          TEXT        NOT NULL DEFAULT '1d',
    start_date        DATE        NOT NULL,
    end_date          DATE        NOT NULL,

    -- Under what assumptions
    cost_model        JSONB       NOT NULL DEFAULT '{}'::jsonb,
    backtest_config   JSONB       NOT NULL DEFAULT '{}'::jsonb,

    -- Against what data, from what code
    data_version      TEXT        NOT NULL,
    code_version      TEXT        NOT NULL DEFAULT 'dev',
    contract_version  INTEGER     NOT NULL DEFAULT 1,

    -- What came out
    result            JSONB       NOT NULL,

    created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),

    CONSTRAINT experiments_date_range_valid CHECK (start_date <= end_date)
);

-- The journal and experiment list are ordered newest-first; without this every
-- page load sorts the whole table.
CREATE INDEX experiments_created_at_idx ON experiments (created_at DESC);

-- "Show me every run of this strategy on this instrument" is the comparison
-- view's primary query.
CREATE INDEX experiments_strategy_symbol_idx ON experiments (strategy, symbol);

-- Reproducibility checks look up prior runs by the exact data they used.
CREATE INDEX experiments_data_version_idx ON experiments (data_version);

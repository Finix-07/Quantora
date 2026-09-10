-- 000003_portfolios (down)
--
-- Dropping `portfolios` cascades to `positions`, which destroys the user's
-- holdings — the input every saved risk report was derived from
-- (maintenance-operations.md §6). Snapshot the Postgres volume before rolling
-- back. Order matters: the child table goes first so the drop does not depend
-- on the cascade behaving as expected.

DROP INDEX IF EXISTS portfolios_created_at_idx;
DROP INDEX IF EXISTS positions_symbol_idx;
DROP INDEX IF EXISTS positions_portfolio_idx;
DROP TABLE IF EXISTS positions;
DROP TABLE IF EXISTS portfolios;

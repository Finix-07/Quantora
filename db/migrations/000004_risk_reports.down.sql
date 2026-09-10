-- 000004_risk_reports (down)
--
-- Dropping this table destroys every saved risk report. The reports are not
-- recomputable: each one describes the market as it stood on the day it was run
-- and re-running the same portfolio produces different numbers
-- (maintenance-operations.md §6). Snapshot the Postgres volume before rolling
-- back.

DROP INDEX IF EXISTS risk_reports_portfolio_idx;
DROP INDEX IF EXISTS risk_reports_created_at_idx;
DROP TABLE IF EXISTS risk_reports;

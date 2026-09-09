-- 000002_experiments (down)
--
-- Dropping this table destroys every saved experiment, which is the system of
-- record for reproducibility (maintenance-operations.md §6). Snapshot the
-- Postgres volume before rolling back.

DROP INDEX IF EXISTS experiments_data_version_idx;
DROP INDEX IF EXISTS experiments_strategy_symbol_idx;
DROP INDEX IF EXISTS experiments_created_at_idx;
DROP TABLE IF EXISTS experiments;

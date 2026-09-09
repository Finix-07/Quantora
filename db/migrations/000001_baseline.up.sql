-- 000001_baseline
--
-- M1 creates no schema objects: the data layer (M2) and the experiment /
-- portfolio tables (M3.4, M5.3, M5.5) own their own migrations.
--
-- This baseline exists so that applying migrations creates golang-migrate's
-- `schema_migrations` bookkeeping table. The Go API refuses to serve traffic
-- until that table exists and is clean, which turns "nobody ran the migrations"
-- into a loud startup failure instead of a confusing 500 on the first query
-- (requirements.md NFR5.6).

SELECT 1;

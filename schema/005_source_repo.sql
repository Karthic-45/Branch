-- Which real repository a root branch's memory was ingested from, so a
-- migration started on it evaluates candidate patches against that same
-- repository instead of a server-wide default. NULL on every row ingested
-- before self-service ingest existed (and on anything ingested via the CLI
-- script directly) -- start_migration() falls back to the
-- BMEM_TARGET_REPO/BMEM_TARGET_REF/BMEM_TEST_SELECTOR env vars in that case,
-- unchanged from before this column existed.
ALTER TABLE branches ADD COLUMN IF NOT EXISTS source_repo STRING;
ALTER TABLE branches ADD COLUMN IF NOT EXISTS source_ref STRING;
ALTER TABLE branches ADD COLUMN IF NOT EXISTS test_selector STRING;

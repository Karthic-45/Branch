-- Cluster-level prerequisites. Run once, as an admin user, before the migrations.
--
-- Requires CockroachDB Standard or Advanced: Basic does not permit these
-- cluster settings or zone configuration changes.

-- Distributed vector indexing is gated behind a cluster setting.
SET CLUSTER SETTING feature.vector_index.enabled = true;

CREATE DATABASE IF NOT EXISTS branchable;

-- Time travel reaches back exactly as far as the garbage-collection window.
-- The modern default is 14400s (4 hours), which is too short to demo an audit
-- trail and too short for a revert that happens the next morning.
--
-- This is a real trade-off, not a free win: a longer window retains more MVCC
-- versions and therefore costs storage. 24h is the smallest value that makes
-- "what did this branch believe yesterday?" an honest question to ask.
--
-- Note this only extends the window. Any claim that survives BEYOND it must be
-- served from merge_log.snapshot, not from AS OF SYSTEM TIME. See 003.
ALTER DATABASE branchable CONFIGURE ZONE USING gc.ttlseconds = 86400;

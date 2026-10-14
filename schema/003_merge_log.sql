-- The record that makes a merge reversible.

CREATE TABLE IF NOT EXISTS merge_log (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    parent_id     UUID NOT NULL REFERENCES branches(id),
    winner_id     UUID NOT NULL REFERENCES branches(id),

    -- The cluster HLC captured immediately BEFORE the merge wrote anything.
    -- Feeds AS OF SYSTEM TIME to reconstruct the parent's exact pre-merge
    -- belief state -- but only while that timestamp is still inside the GC
    -- window (24h, per 000_bootstrap.sql).
    pre_merge_hlc DECIMAL NOT NULL,

    -- The same pre-merge state, materialized.
    --
    -- MVCC history is garbage-collected; a merge from last week is no longer
    -- reachable by time travel. Storing the snapshot means revert keeps working
    -- after the window closes, and the audit trail is durable rather than
    -- best-effort. AOST is the elegant path, this is the honest one -- the demo
    -- shows the first, production depends on the second.
    --
    -- Shape: [{"key": ..., "content": ..., "tombstone": ...}, ...]
    -- Embeddings are excluded: they are large, and they are recomputable from
    -- content. Reverting re-embeds.
    snapshot      JSONB NOT NULL,

    merged_at     TIMESTAMPTZ NOT NULL DEFAULT now(),

    -- Set when this merge has itself been undone, so the log stays append-only
    -- and a branch's full history remains inspectable.
    reverted_at   TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS merge_log_by_parent ON merge_log (parent_id, merged_at DESC);

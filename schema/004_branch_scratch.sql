-- Working notes an agent takes while exploring a branch.
--
-- This is the reasoning that should NOT outlive the hypothesis. When a branch
-- is abandoned, its scratch notes are not "corrected" or "down-weighted" --
-- the database reclaims the rows and a future semantic search cannot retrieve
-- them, because they no longer exist.
--
-- Row-level TTL means that happens with no cron job and no application code.

CREATE TABLE IF NOT EXISTS branch_scratch (
    branch_id  UUID NOT NULL REFERENCES branches(id),
    seq        INT NOT NULL,
    note       TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),

    PRIMARY KEY (branch_id, seq)
) WITH (ttl_expire_after = '24 hours');

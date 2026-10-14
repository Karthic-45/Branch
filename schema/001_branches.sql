-- A branch is lineage, not a copy.
--
-- Forking writes exactly one row. No memory is duplicated, so the cost of
-- exploring fifty hypotheses is fifty small inserts.

CREATE TABLE IF NOT EXISTS branches (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    parent_id   UUID REFERENCES branches(id),

    -- The cluster's hybrid logical clock at the instant of the split.
    -- Everything the parent knew at or before this point is inherited;
    -- everything it writes after is invisible to this branch.
    fork_point  DECIMAL NOT NULL,

    hypothesis  TEXT,
    status      STRING NOT NULL DEFAULT 'exploring',
    score       FLOAT,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),

    CONSTRAINT status_is_known CHECK (status IN ('exploring', 'merged', 'abandoned')),
    CONSTRAINT score_is_a_rate CHECK (score IS NULL OR (score >= 0.0 AND score <= 1.0)),

    -- The root branch is the only one allowed to have no parent.
    CONSTRAINT only_root_is_parentless CHECK (
        (parent_id IS NULL AND fork_point = 0) OR parent_id IS NOT NULL
    )
);

-- Ancestry resolution walks child -> parent, but the changefeed consumer and
-- the "retire the siblings" step of a merge both walk parent -> children.
CREATE INDEX IF NOT EXISTS branches_by_parent ON branches (parent_id, status);

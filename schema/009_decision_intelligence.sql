-- Why each branch won or lost, and why the winner was the winner.
--
-- branches.score already records *what* each branch scored. Neither table
-- here duplicates that: they record the parts that were previously computed
-- in memory inside run_migration() and then thrown away -- the losing
-- branches' failure reasons (so a future run can avoid re-attempting a
-- known-dead approach) and the selection rationale (so "why B and not A" has
-- a recorded answer rather than being re-derived, differently, each time
-- someone asks).

-- One row per evaluated branch, winner and losers alike. Failure memory:
-- deliberately keeps the losers, which is the whole point.
CREATE TABLE IF NOT EXISTS branch_outcomes (
    branch_id      UUID PRIMARY KEY REFERENCES branches(id),
    run_id         STRING NOT NULL,
    parent_id      UUID,
    strategy_name  STRING,
    hypothesis     STRING,
    outcome        STRING NOT NULL,   -- 'won' | 'lost'
    score          FLOAT,
    passed         INT,
    total          INT,
    reason         STRING,            -- evaluate.py's own reason, e.g. patch_did_not_apply
    score_gap      FLOAT,             -- points behind the winner; 0.0 for the winner
    patch_stats    JSONB,             -- bmem.patchstats.analyze_patch of this branch's diff
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS branch_outcomes_by_parent
    ON branch_outcomes (parent_id, created_at DESC);

-- One row per merge: how the winner was chosen, including whether the
-- decision was actually close enough to need a tie-breaker.
CREATE TABLE IF NOT EXISTS decision_rationales (
    merge_id             UUID PRIMARY KEY REFERENCES merge_log(id),
    run_id               STRING NOT NULL,
    winner_branch_id     UUID NOT NULL,
    winner_score         FLOAT,
    runner_up_branch_id  UUID,
    runner_up_score      FLOAT,
    margin               FLOAT,
    tie_detected         BOOL NOT NULL DEFAULT false,
    discriminator        STRING,      -- what broke the tie; NULL when the score decided it
    rationale            STRING NOT NULL,
    strategy_similarity  JSONB,       -- pairwise cosine over the real hypothesis embeddings
    created_at           TIMESTAMPTZ NOT NULL DEFAULT now()
);

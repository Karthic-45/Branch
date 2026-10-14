-- One row per explored branch: exactly what was retrieved, which model
-- answered, and (once evaluation finishes) what it was tested against --
-- enough to answer "could this be replayed" without guessing. branch_id is
-- the primary key, not run_id, because reproducibility is a property of one
-- branch's one attempt, matching execution_manifests' 1:1 relationship to
-- branches.
CREATE TABLE IF NOT EXISTS execution_manifests (
    branch_id            UUID PRIMARY KEY REFERENCES branches(id),
    run_id               STRING NOT NULL,
    strategy_name        STRING NOT NULL,
    hypothesis           STRING,

    -- explore.py, written when the branch is explored
    reasoning_provider      STRING,
    reasoning_model_id      STRING,
    explore_prompt_version  STRING,
    embedding_provider      STRING,
    embedding_model_id      STRING,
    retrieved_memories      JSONB,

    -- evaluate.py, written by orchestrate.py once evaluation finishes --
    -- absent (NULL) until then, same "not there yet" honesty as memory.s3_uri.
    target_repo        STRING,
    target_ref          STRING,
    base_sha             STRING,
    test_selector          STRING,
    codebuild_project        STRING,

    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at  TIMESTAMPTZ
);

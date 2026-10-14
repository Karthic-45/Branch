-- Every attempt to apply a winning patch to a real checkout -- not just the
-- latest one. A rejected base_changed attempt, a failed validation, and the
-- eventual successful apply are all separate audit-worthy events; this table
-- is append-only the same way merge_log is, for the same reason (Principle 4
-- in the spec this schema was built against: never lose decision provenance).
CREATE TABLE IF NOT EXISTS decision_applications (
    id                   UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    merge_id             UUID NOT NULL REFERENCES merge_log(id),
    base_sha             STRING,
    current_head_sha     STRING,
    base_changed         BOOL NOT NULL DEFAULT false,
    branch               STRING,
    patch_applied        BOOL NOT NULL DEFAULT false,
    conflicts            JSONB,
    validation_status    STRING NOT NULL,
    validation_steps     JSONB,
    commit_sha           STRING,
    applied_tree_s3_uri  STRING,
    created_at           TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS decision_applications_by_merge
    ON decision_applications (merge_id, created_at DESC);

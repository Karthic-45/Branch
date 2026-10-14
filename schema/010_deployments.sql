-- Deployment records and the production evidence that followed them.
--
-- Deploying is a human-clicked action in this system, never policy-triggered:
-- an agent that can promote its own belief AND ship it to production without
-- a person in the loop is a different and much larger safety claim than this
-- project makes. What is automated is the *record* -- so "which decision is
-- live, and did it do what the evaluation suggested" has an answer.

CREATE TABLE IF NOT EXISTS deployments (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    merge_id        UUID NOT NULL REFERENCES merge_log(id),
    commit_sha      STRING,
    environment     STRING NOT NULL,   -- development | staging | production
    status          STRING NOT NULL,   -- pending | running | healthy | failed | rolled_back
    target          STRING,            -- e.g. the Elastic Beanstalk environment name
    artifact        STRING,            -- S3 URI of what was shipped, when known
    detail          STRING,            -- human note or failure summary
    started_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    completed_at    TIMESTAMPTZ,
    rolled_back_at  TIMESTAMPTZ,
    superseded_by   UUID,              -- the deployment that replaced this one
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS deployments_by_merge ON deployments (merge_id, created_at DESC);
CREATE INDEX IF NOT EXISTS deployments_by_env ON deployments (environment, created_at DESC);

-- Measurements taken around a deployment. `source` is mandatory and there is
-- deliberately no default: every number here has to say where it came from,
-- because the one thing this table must never do is let an estimate be read
-- back later as an observation.
CREATE TABLE IF NOT EXISTS production_observations (
    id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    deployment_id  UUID NOT NULL REFERENCES deployments(id),
    metric         STRING NOT NULL,    -- e.g. p95_latency_ms, error_rate
    phase          STRING NOT NULL,    -- before | after
    value          FLOAT NOT NULL,
    unit           STRING,
    source         STRING NOT NULL,    -- cloudwatch | manual
    note           STRING,
    observed_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS observations_by_deployment
    ON production_observations (deployment_id, metric, phase);

-- Branch-scoped, semantically searchable memory.

CREATE TABLE IF NOT EXISTS memory (
    branch_id      UUID NOT NULL REFERENCES branches(id),
    key            STRING NOT NULL,
    content        TEXT,

    -- Amazon Titan Text Embeddings v2, 1024 dimensions.
    embedding      VECTOR(1024),

    -- The write's own HLC. This column is what makes branch-scoped retrieval
    -- possible in a single query.
    --
    -- The obvious design reads each ancestor AS OF SYSTEM TIME its fork point,
    -- but AOST applies to an entire statement and every subquery must share one
    -- timestamp, so an N-deep chain needing N timestamps cannot be expressed.
    -- Recording the write time on the row instead turns "read the parent as it
    -- was at the fork" into an ordinary predicate: created_at_hlc <= horizon.
    created_at_hlc DECIMAL NOT NULL DEFAULT cluster_logical_timestamp(),

    -- Branch-local deletion. A branch shadows an inherited key by writing its
    -- own row with tombstone = true. The row must survive retrieval's WHERE
    -- clause so that dedupe can see it out-rank the ancestor's copy -- filtering
    -- tombstones in SQL would let the inherited value through instead.
    tombstone      BOOL NOT NULL DEFAULT false,

    -- Provenance: where the underlying artifact lives in S3.
    s3_uri         STRING,

    PRIMARY KEY (branch_id, key)
);

-- branch_id is a PREFIX column, and that is the entire point.
--
-- CockroachDB accelerates a filtered ANN search only when every prefix column
-- is constrained by = or IN. With branch_id first, "search only this branch's
-- lineage" is an index-level restriction rather than a post-filter over the
-- whole corpus -- which is what lets four agents share one index without
-- contaminating each other.
CREATE VECTOR INDEX IF NOT EXISTS memory_by_branch_embedding
    ON memory (branch_id, embedding);

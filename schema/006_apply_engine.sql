-- The commit a candidate patch was actually evaluated against, and the
-- repository's own validation profile (install/test/lint/typecheck/build
-- commands), both needed by the apply engine to (a) refuse to apply a patch
-- onto a repository that has moved since it was tested, and (b) know how to
-- validate a non-Python/pytest project instead of hardcoding one toolchain.
ALTER TABLE memory ADD COLUMN IF NOT EXISTS base_sha STRING;
ALTER TABLE branches ADD COLUMN IF NOT EXISTS validation_profile JSONB;

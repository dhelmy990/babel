ALTER TABLE babel_sources
  DROP CONSTRAINT babel_sources_provider_check;

ALTER TABLE babel_sources
  ADD COLUMN source_repository text,
  ADD COLUMN source_config text,
  ADD COLUMN source_commit_sha text,
  ADD COLUMN source_article_key text,
  ADD COLUMN source_snapshot_date text,
  ADD COLUMN source_content_sha256 text,
  ADD CONSTRAINT babel_sources_provider_check
    CHECK (provider IN ('wikipedia', 'huggingface_wikipedia')),
  ADD CONSTRAINT babel_sources_huggingface_provenance_check
    CHECK (
      (provider = 'wikipedia' AND
       source_repository IS NULL AND source_config IS NULL AND
       source_commit_sha IS NULL AND source_article_key IS NULL AND
       source_snapshot_date IS NULL AND source_content_sha256 IS NULL)
      OR
      (provider = 'huggingface_wikipedia' AND
       char_length(source_repository) > 0 AND char_length(source_config) > 0 AND
       source_commit_sha ~ '^[0-9a-f]{40}$' AND char_length(source_article_key) > 0 AND
       char_length(source_snapshot_date) > 0 AND
       source_content_sha256 ~ '^[0-9a-f]{64}$')
    );

ALTER TABLE seed_runs
  ADD COLUMN source_repository text,
  ADD COLUMN source_config text,
  ADD COLUMN source_commit_sha text,
  ADD COLUMN source_snapshot_date text,
  ADD CONSTRAINT seed_runs_source_pin_check
    CHECK (
      (source_repository IS NULL AND source_config IS NULL AND
       source_commit_sha IS NULL AND source_snapshot_date IS NULL)
      OR
      (char_length(source_repository) > 0 AND char_length(source_config) > 0 AND
       source_commit_sha ~ '^[0-9a-f]{40}$' AND char_length(source_snapshot_date) > 0)
    );

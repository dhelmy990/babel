ALTER TABLE experiment_babels
  ADD COLUMN article_text text,
  ADD COLUMN catalog_content_hash text CHECK (
    catalog_content_hash IS NULL OR catalog_content_hash ~ '^[0-9a-f]{64}$'
  ),
  ADD COLUMN event_number bigint CHECK (event_number IS NULL OR event_number >= 0),
  ADD COLUMN request_id uuid,
  ADD COLUMN finalized_at timestamptz;

CREATE TABLE babel_embeddings (
  run_id uuid NOT NULL REFERENCES experiment_runs(id) ON DELETE RESTRICT,
  babel_id uuid NOT NULL,
  creator_id uuid NOT NULL,
  embedding_space_id uuid NOT NULL,
  serving_model_id uuid NOT NULL REFERENCES recommender_models(id) ON DELETE RESTRICT,
  materialized_model_version bigint NOT NULL CHECK (materialized_model_version >= 0),
  catalog_content_hash text NOT NULL CHECK (catalog_content_hash ~ '^[0-9a-f]{64}$'),
  embedding public.vector(100) NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (run_id, babel_id, materialized_model_version),
  FOREIGN KEY (run_id, babel_id) REFERENCES experiment_babels(run_id, babel_id)
    ON DELETE RESTRICT
);

CREATE INDEX babel_embeddings_cosine_hnsw
  ON babel_embeddings USING hnsw (embedding public.vector_cosine_ops);
CREATE INDEX babel_embeddings_active_lookup
  ON babel_embeddings (run_id, serving_model_id, materialized_model_version DESC);

CREATE TABLE run_embedding_states (
  run_id uuid PRIMARY KEY REFERENCES experiment_runs(id) ON DELETE RESTRICT,
  active_model_id uuid NOT NULL REFERENCES recommender_models(id) ON DELETE RESTRICT,
  active_model_version bigint NOT NULL CHECK (active_model_version >= 0),
  embedding_space_id uuid NOT NULL,
  pgvector_snapshot_sha256 text NOT NULL CHECK (
    pgvector_snapshot_sha256 ~ '^[0-9a-f]{64}$'
  ),
  backend_snapshot_sha256 text NOT NULL CHECK (
    backend_snapshot_sha256 ~ '^[0-9a-f]{64}$'
  ),
  synchronized_at timestamptz NOT NULL DEFAULT now()
);

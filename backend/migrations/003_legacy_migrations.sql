CREATE TABLE legacy_migrations (
  source_sha256 text PRIMARY KEY CHECK (source_sha256 ~ '^[0-9a-f]{64}$'),
  creator_id uuid NOT NULL REFERENCES creators(id) ON DELETE RESTRICT,
  babel_count integer NOT NULL CHECK (babel_count >= 0),
  edge_count integer NOT NULL CHECK (edge_count >= 0),
  completed_at timestamptz NOT NULL DEFAULT now()
);

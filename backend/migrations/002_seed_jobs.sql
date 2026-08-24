CREATE TABLE seed_runs (
  id uuid PRIMARY KEY,
  manifest_version text NOT NULL CHECK (char_length(manifest_version) > 0),
  state text NOT NULL CHECK (state IN (
    'queued', 'running', 'completed', 'completed_with_errors', 'failed', 'interrupted'
  )),
  total integer NOT NULL CHECK (total > 0),
  started_at timestamptz,
  finished_at timestamptz,
  created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE seed_run_items (
  seed_run_id uuid NOT NULL REFERENCES seed_runs(id) ON DELETE CASCADE,
  seed_assignment_id uuid NOT NULL,
  creator_id uuid NOT NULL REFERENCES creators(id) ON DELETE RESTRICT,
  declared_title text NOT NULL CHECK (char_length(declared_title) > 0),
  resolved_page_id bigint CHECK (resolved_page_id > 0),
  babel_id uuid,
  state text NOT NULL CHECK (state IN (
    'pending', 'resolving', 'importing', 'imported', 'skipped', 'failed'
  )),
  attempt_count integer NOT NULL DEFAULT 0 CHECK (attempt_count >= 0),
  error_code text,
  error_detail text,
  started_at timestamptz,
  finished_at timestamptz,
  PRIMARY KEY (seed_run_id, seed_assignment_id),
  CONSTRAINT seed_run_items_imported_result_check
    CHECK (state <> 'imported' OR (resolved_page_id IS NOT NULL AND babel_id IS NOT NULL)),
  CONSTRAINT seed_run_items_failed_error_check
    CHECK (state <> 'failed' OR (error_code IS NOT NULL AND char_length(error_code) > 0))
);

ALTER TABLE seed_run_items ADD CONSTRAINT seed_run_items_babel_owner_fk
  FOREIGN KEY (creator_id, babel_id) REFERENCES babels(owner_id, id) ON DELETE RESTRICT;

CREATE INDEX seed_run_items_run_state_index ON seed_run_items(seed_run_id, state);

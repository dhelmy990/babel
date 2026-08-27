CREATE TABLE recommender_models (
  id uuid PRIMARY KEY,
  label text NOT NULL CHECK (char_length(label) > 0),
  parent_model_id uuid REFERENCES recommender_models(id) ON DELETE RESTRICT,
  producing_run_id uuid,
  encoder_repo text NOT NULL CHECK (char_length(encoder_repo) > 0),
  encoder_revision text NOT NULL CHECK (encoder_revision ~ '^[0-9a-f]{40,64}$'),
  dataset_repo text NOT NULL CHECK (char_length(dataset_repo) > 0),
  dataset_revision text NOT NULL CHECK (dataset_revision ~ '^[0-9a-f]{40,64}$'),
  environment_sequence jsonb NOT NULL CHECK (jsonb_typeof(environment_sequence) = 'array'),
  training_examples bigint NOT NULL DEFAULT 0 CHECK (training_examples >= 0),
  checkpoint_path text NOT NULL CHECK (char_length(checkpoint_path) > 0),
  checkpoint_sha256 text NOT NULL CHECK (checkpoint_sha256 ~ '^[0-9a-f]{64}$'),
  embedding_space jsonb NOT NULL CHECK (jsonb_typeof(embedding_space) = 'object'),
  immutable boolean NOT NULL DEFAULT true CHECK (immutable),
  created_at timestamptz NOT NULL DEFAULT now(),
  CHECK (
    (parent_model_id IS NULL AND producing_run_id IS NULL) OR
    (parent_model_id IS NOT NULL AND producing_run_id IS NOT NULL)
  )
);

CREATE TABLE experiment_runs (
  id uuid PRIMARY KEY,
  status text NOT NULL CHECK (status IN (
    'starting', 'running', 'stop_requested', 'draining_feedback',
    'checkpointing', 'exporting_interactions', 'completed', 'failed', 'interrupted'
  )),
  retrieval_backend text NOT NULL
    CHECK (retrieval_backend IN ('pgvector', 'hnswlib')),
  creator_count integer NOT NULL CHECK (creator_count > 0 AND creator_count <= 10000),
  scenario text NOT NULL CHECK (scenario IN ('june_only', 'june_to_july')),
  environment_sequence jsonb NOT NULL CHECK (
    environment_sequence = '["2026-06"]'::jsonb OR
    environment_sequence = '["2026-06", "2026-07"]'::jsonb
  ),
  event_budget_per_month integer NOT NULL
    CHECK (event_budget_per_month > 0 AND event_budget_per_month <= 1000000),
  run_seed bigint NOT NULL CHECK (run_seed >= 0),
  dataset_repository text NOT NULL CHECK (char_length(dataset_repository) > 0),
  dataset_config text NOT NULL CHECK (char_length(dataset_config) > 0),
  dataset_revision text NOT NULL CHECK (dataset_revision ~ '^[0-9a-f]{40,64}$'),
  recommendation_k integer NOT NULL DEFAULT 10 CHECK (recommendation_k > 0 AND recommendation_k <= 100),
  top_l integer NOT NULL DEFAULT 100 CHECK (top_l > 0),
  kafka_topic text NOT NULL DEFAULT 'babel.feedback.v1' CHECK (char_length(kafka_topic) > 0),
  kafka_group text NOT NULL DEFAULT 'babel-online-trainer-v1' CHECK (char_length(kafka_group) > 0),
  checkpoint_every_events integer NOT NULL DEFAULT 100 CHECK (checkpoint_every_events > 0),
  sync_every_steps integer NOT NULL DEFAULT 10 CHECK (sync_every_steps > 0),
  artifact_root text NOT NULL DEFAULT 'artifacts/online' CHECK (char_length(artifact_root) > 0),
  state_root text NOT NULL DEFAULT 'state/online' CHECK (char_length(state_root) > 0),
  starting_model_id uuid NOT NULL REFERENCES recommender_models(id) ON DELETE RESTRICT,
  active_model_id uuid NOT NULL REFERENCES recommender_models(id) ON DELETE RESTRICT,
  active_model_version bigint NOT NULL DEFAULT 0 CHECK (active_model_version >= 0),
  launch_config jsonb NOT NULL CHECK (jsonb_typeof(launch_config) = 'object'),
  launch_sha256 text NOT NULL CHECK (launch_sha256 ~ '^[0-9a-f]{64}$'),
  created_babel_count bigint NOT NULL DEFAULT 0 CHECK (created_babel_count >= 0),
  feedback_count bigint NOT NULL DEFAULT 0 CHECK (feedback_count >= 0),
  event_rate double precision NOT NULL DEFAULT 0 CHECK (event_rate >= 0),
  kafka_offset bigint NOT NULL DEFAULT 0 CHECK (kafka_offset >= 0),
  kafka_lag bigint NOT NULL DEFAULT 0 CHECK (kafka_lag >= 0),
  trainer_steps bigint NOT NULL DEFAULT 0 CHECK (trainer_steps >= 0),
  rolling_rank_loss double precision,
  checkpoint_path text,
  checkpoint_sha256 text CHECK (
    checkpoint_sha256 IS NULL OR checkpoint_sha256 ~ '^[0-9a-f]{64}$'
  ),
  serving_synced boolean NOT NULL DEFAULT true,
  failure text,
  started_at timestamptz,
  completed_at timestamptz,
  stop_requested_at timestamptz,
  created_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE (id, retrieval_backend)
);

ALTER TABLE recommender_models
  ADD CONSTRAINT recommender_models_producing_run_fk
  FOREIGN KEY (producing_run_id) REFERENCES experiment_runs(id) ON DELETE RESTRICT;

CREATE UNIQUE INDEX experiment_runs_one_active
  ON experiment_runs ((true))
  WHERE status IN (
    'starting', 'running', 'stop_requested', 'draining_feedback',
    'checkpointing', 'exporting_interactions'
  );

CREATE TABLE experiment_babels (
  run_id uuid NOT NULL REFERENCES experiment_runs(id) ON DELETE RESTRICT,
  babel_id uuid NOT NULL,
  creator_id uuid NOT NULL,
  source_article_key text NOT NULL CHECK (char_length(source_article_key) > 0),
  title text NOT NULL CHECK (char_length(title) > 0),
  created_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (run_id, babel_id),
  UNIQUE (run_id, creator_id, source_article_key)
);

CREATE TABLE experiment_activity_logs (
  run_id uuid NOT NULL REFERENCES experiment_runs(id) ON DELETE CASCADE,
  sequence bigint NOT NULL CHECK (sequence > 0),
  occurred_at_ns bigint NOT NULL CHECK (occurred_at_ns >= 0),
  level text NOT NULL CHECK (level IN ('debug', 'info', 'warning', 'error')),
  component text NOT NULL CHECK (component IN ('supervisor', 'serving', 'training', 'feedback')),
  event text NOT NULL CHECK (char_length(event) > 0),
  message text NOT NULL CHECK (char_length(message) > 0),
  metrics jsonb NOT NULL DEFAULT '{}'::jsonb CHECK (jsonb_typeof(metrics) = 'object'),
  details jsonb NOT NULL CHECK (jsonb_typeof(details) = 'object'),
  PRIMARY KEY (run_id, sequence)
);

CREATE INDEX experiment_activity_logs_recent
  ON experiment_activity_logs (run_id, sequence);
CREATE INDEX experiment_runs_model_created
  ON experiment_runs (starting_model_id, created_at DESC);

CREATE FUNCTION prevent_recommender_model_mutation()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  RAISE EXCEPTION 'recommender models are immutable';
END;
$$;

CREATE TRIGGER recommender_models_immutable
BEFORE UPDATE OR DELETE ON recommender_models
FOR EACH ROW EXECUTE FUNCTION prevent_recommender_model_mutation();

CREATE FUNCTION prevent_experiment_launch_mutation()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF NEW.retrieval_backend IS DISTINCT FROM OLD.retrieval_backend OR
     NEW.creator_count IS DISTINCT FROM OLD.creator_count OR
     NEW.scenario IS DISTINCT FROM OLD.scenario OR
     NEW.environment_sequence IS DISTINCT FROM OLD.environment_sequence OR
     NEW.event_budget_per_month IS DISTINCT FROM OLD.event_budget_per_month OR
     NEW.run_seed IS DISTINCT FROM OLD.run_seed OR
     NEW.dataset_repository IS DISTINCT FROM OLD.dataset_repository OR
     NEW.dataset_config IS DISTINCT FROM OLD.dataset_config OR
     NEW.dataset_revision IS DISTINCT FROM OLD.dataset_revision OR
     NEW.recommendation_k IS DISTINCT FROM OLD.recommendation_k OR
     NEW.top_l IS DISTINCT FROM OLD.top_l OR
     NEW.kafka_topic IS DISTINCT FROM OLD.kafka_topic OR
     NEW.kafka_group IS DISTINCT FROM OLD.kafka_group OR
     NEW.checkpoint_every_events IS DISTINCT FROM OLD.checkpoint_every_events OR
     NEW.sync_every_steps IS DISTINCT FROM OLD.sync_every_steps OR
     NEW.artifact_root IS DISTINCT FROM OLD.artifact_root OR
     NEW.state_root IS DISTINCT FROM OLD.state_root OR
     NEW.starting_model_id IS DISTINCT FROM OLD.starting_model_id OR
     NEW.launch_config IS DISTINCT FROM OLD.launch_config OR
     NEW.launch_sha256 IS DISTINCT FROM OLD.launch_sha256 THEN
    RAISE EXCEPTION 'experiment launch configuration is immutable';
  END IF;
  RETURN NEW;
END;
$$;

CREATE TRIGGER experiment_runs_launch_immutable
BEFORE UPDATE ON experiment_runs
FOR EACH ROW EXECUTE FUNCTION prevent_experiment_launch_mutation();

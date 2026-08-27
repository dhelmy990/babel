ALTER TABLE experiment_runs
  ADD COLUMN contract_version integer NOT NULL DEFAULT 1
    CHECK (contract_version IN (1, 2)),
  ADD COLUMN source_articles_per_month integer NOT NULL DEFAULT 80
    CHECK (source_articles_per_month > 0 AND source_articles_per_month <= 1000000),
  ADD COLUMN target_created_babels integer NOT NULL DEFAULT 1
    CHECK (target_created_babels > 0 AND target_created_babels <= 1000000),
  ADD COLUMN concurrent_users integer NOT NULL DEFAULT 1
    CHECK (concurrent_users > 0 AND concurrent_users <= creator_count),
  ADD COLUMN recommendation_start_probability double precision NOT NULL DEFAULT 0.4
    CHECK (recommendation_start_probability >= 0 AND recommendation_start_probability <= 1),
  ADD COLUMN continuation_probability double precision NOT NULL DEFAULT 0.4
    CHECK (continuation_probability >= 0 AND continuation_probability <= 1),
  ADD COLUMN maximum_traversal_depth integer NOT NULL DEFAULT 2
    CHECK (maximum_traversal_depth = 2),
  ADD COLUMN maximum_requests_per_traversal integer NOT NULL DEFAULT 10
    CHECK (maximum_requests_per_traversal > 0 AND maximum_requests_per_traversal <= 10),
  ADD COLUMN interleave_creation_and_recommendations boolean NOT NULL DEFAULT true,
  ADD COLUMN source_vector_qwen_encode_count bigint NOT NULL DEFAULT 0
    CHECK (source_vector_qwen_encode_count >= 0),
  ADD COLUMN source_vector_cache_hit_count bigint NOT NULL DEFAULT 0
    CHECK (source_vector_cache_hit_count >= 0),
  ADD COLUMN source_vector_pgvector_load_count bigint NOT NULL DEFAULT 0
    CHECK (source_vector_pgvector_load_count >= 0),
  ADD COLUMN source_vector_eviction_count bigint NOT NULL DEFAULT 0
    CHECK (source_vector_eviction_count >= 0);

CREATE TABLE experiment_work_schedule (
  run_id uuid NOT NULL REFERENCES experiment_runs(id) ON DELETE RESTRICT,
  schedule_index bigint NOT NULL CHECK (schedule_index >= 0),
  creator_id uuid NOT NULL,
  creator_event_number bigint NOT NULL CHECK (creator_event_number >= 0),
  period text NOT NULL CHECK (period IN ('2026-06', '2026-07')),
  source_article_key text NOT NULL CHECK (char_length(source_article_key) > 0),
  root_babel_id uuid NOT NULL,
  traversal_session_id uuid NOT NULL,
  work_id uuid NOT NULL,
  workload_sha256 text NOT NULL CHECK (workload_sha256 ~ '^[0-9a-f]{64}$'),
  created_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (run_id, schedule_index),
  UNIQUE (run_id, creator_id, creator_event_number),
  UNIQUE (run_id, creator_id, source_article_key),
  UNIQUE (run_id, root_babel_id),
  UNIQUE (run_id, traversal_session_id),
  UNIQUE (run_id, work_id)
);

CREATE TABLE experiment_edges (
  run_id uuid NOT NULL REFERENCES experiment_runs(id) ON DELETE RESTRICT,
  source_babel_id uuid NOT NULL,
  target_babel_id uuid NOT NULL,
  acting_creator_id uuid NOT NULL,
  request_id uuid NOT NULL,
  feedback_event_id uuid NOT NULL,
  feedback_occurred_at_ns bigint NOT NULL CHECK (feedback_occurred_at_ns >= 0),
  traversal_session_id uuid NOT NULL,
  traversal_depth integer NOT NULL CHECK (traversal_depth BETWEEN 1 AND 2),
  created_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (run_id, source_babel_id, target_babel_id),
  CHECK (source_babel_id <> target_babel_id),
  FOREIGN KEY (run_id, source_babel_id)
    REFERENCES experiment_babels(run_id, babel_id) ON DELETE RESTRICT,
  FOREIGN KEY (run_id, target_babel_id)
    REFERENCES experiment_babels(run_id, babel_id) ON DELETE RESTRICT
);

CREATE INDEX experiment_edges_target
  ON experiment_edges (run_id, target_babel_id);
CREATE INDEX experiment_edges_feedback_order
  ON experiment_edges (run_id, feedback_occurred_at_ns, feedback_event_id);

CREATE FUNCTION prevent_experiment_work_schedule_mutation()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  RAISE EXCEPTION 'experiment work schedules are immutable';
END;
$$;

CREATE TRIGGER experiment_work_schedule_immutable
BEFORE UPDATE OR DELETE ON experiment_work_schedule
FOR EACH ROW EXECUTE FUNCTION prevent_experiment_work_schedule_mutation();

CREATE OR REPLACE FUNCTION prevent_experiment_launch_mutation()
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
     NEW.launch_sha256 IS DISTINCT FROM OLD.launch_sha256 OR
     NEW.contract_version IS DISTINCT FROM OLD.contract_version OR
     NEW.source_articles_per_month IS DISTINCT FROM OLD.source_articles_per_month OR
     NEW.target_created_babels IS DISTINCT FROM OLD.target_created_babels OR
     NEW.concurrent_users IS DISTINCT FROM OLD.concurrent_users OR
     NEW.recommendation_start_probability IS DISTINCT FROM OLD.recommendation_start_probability OR
     NEW.continuation_probability IS DISTINCT FROM OLD.continuation_probability OR
     NEW.maximum_traversal_depth IS DISTINCT FROM OLD.maximum_traversal_depth OR
     NEW.maximum_requests_per_traversal IS DISTINCT FROM OLD.maximum_requests_per_traversal OR
     NEW.interleave_creation_and_recommendations IS DISTINCT FROM OLD.interleave_creation_and_recommendations THEN
    RAISE EXCEPTION 'experiment launch configuration is immutable';
  END IF;
  RETURN NEW;
END;
$$;

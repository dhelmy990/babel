CREATE TABLE performance_experiments (
  id uuid PRIMARY KEY,
  status text NOT NULL DEFAULT 'population_pending' CHECK (status IN (
    'population_pending', 'population_ready', 'approved', 'running',
    'stop_requested', 'draining', 'completed', 'failed', 'interrupted'
  )),
  topology text NOT NULL DEFAULT 'same_host_split' CHECK (
    topology IN ('same_process', 'same_host_split', 'same_host_isolated')
  ),
  starting_model_id uuid NOT NULL REFERENCES recommender_models(id) ON DELETE RESTRICT,
  model_repository text NOT NULL,
  model_revision text NOT NULL CHECK (model_revision ~ '^[0-9a-f]{40,64}$'),
  dataset_repository text NOT NULL,
  dataset_revision text NOT NULL CHECK (dataset_revision ~ '^[0-9a-f]{40,64}$'),
  retrieval_backend text NOT NULL DEFAULT 'pgvector' CHECK (
    retrieval_backend IN ('pgvector', 'hnswlib')
  ),
  creator_count integer NOT NULL DEFAULT 50 CHECK (creator_count BETWEEN 1 AND 10000),
  seeded_articles integer NOT NULL DEFAULT 10000 CHECK (seeded_articles BETWEEN 1 AND 1000000),
  target_created_babels integer NOT NULL DEFAULT 10000 CHECK (
    target_created_babels BETWEEN 1 AND 1000000
  ),
  concurrent_users integer NOT NULL DEFAULT 50 CHECK (
    concurrent_users BETWEEN 1 AND 10000
  ),
  recommendation_start_probability double precision NOT NULL DEFAULT 0.4 CHECK (
    recommendation_start_probability BETWEEN 0 AND 1
  ),
  continuation_probability double precision NOT NULL DEFAULT 0.4 CHECK (
    continuation_probability BETWEEN 0 AND 1
  ),
  maximum_traversal_depth integer NOT NULL DEFAULT 2 CHECK (maximum_traversal_depth = 2),
  maximum_requests_per_traversal integer NOT NULL DEFAULT 10 CHECK (
    maximum_requests_per_traversal BETWEEN 1 AND 10
  ),
  training_micro_batch_size integer NOT NULL DEFAULT 8 CHECK (
    training_micro_batch_size BETWEEN 1 AND 1024
  ),
  sync_every_steps integer NOT NULL DEFAULT 10 CHECK (sync_every_steps BETWEEN 1 AND 1000000),
  interleave_creation_and_recommendations boolean NOT NULL DEFAULT true,
  auto_advance boolean NOT NULL DEFAULT false CHECK (auto_advance = false),
  warmup_seconds integer NOT NULL DEFAULT 30 CHECK (warmup_seconds BETWEEN 0 AND 3600),
  duration_seconds integer NOT NULL DEFAULT 120 CHECK (duration_seconds BETWEEN 1 AND 86400),
  target_rps double precision NOT NULL DEFAULT 5 CHECK (target_rps > 0),
  latency_safety_threshold_ms double precision NOT NULL DEFAULT 5000 CHECK (
    latency_safety_threshold_ms > 0
  ),
  placement_manifest jsonb,
  placement_sha256 text CHECK (placement_sha256 IS NULL OR placement_sha256 ~ '^[0-9a-f]{64}$'),
  hardware_identity jsonb NOT NULL DEFAULT '{}'::jsonb,
  resource_identity jsonb NOT NULL DEFAULT '{}'::jsonb,
  request_identity jsonb NOT NULL DEFAULT '{}'::jsonb,
  feedback_identity jsonb NOT NULL DEFAULT '{}'::jsonb,
  population_ready boolean NOT NULL DEFAULT false,
  population_vector_count bigint,
  population_vector_sha256 text CHECK (
    population_vector_sha256 IS NULL OR population_vector_sha256 ~ '^[0-9a-f]{64}$'
  ),
  population_model_repository text,
  population_model_revision text CHECK (
    population_model_revision IS NULL OR population_model_revision ~ '^[0-9a-f]{40,64}$'
  ),
  population_model_sha256 text CHECK (
    population_model_sha256 IS NULL OR population_model_sha256 ~ '^[0-9a-f]{64}$'
  ),
  population_dataset_repository text,
  population_dataset_revision text CHECK (
    population_dataset_revision IS NULL OR population_dataset_revision ~ '^[0-9a-f]{40,64}$'
  ),
  population_dataset_sha256 text CHECK (
    population_dataset_sha256 IS NULL OR population_dataset_sha256 ~ '^[0-9a-f]{64}$'
  ),
  operator_approved boolean NOT NULL DEFAULT false,
  safety_receipt jsonb,
  artifact_sha256 text CHECK (artifact_sha256 IS NULL OR artifact_sha256 ~ '^[0-9a-f]{64}$'),
  remote_hf_commit_sha text CHECK (
    remote_hf_commit_sha IS NULL OR remote_hf_commit_sha ~ '^[0-9a-f]{40,64}$'
  ),
  remote_hf_bundle_path text,
  run_id uuid REFERENCES experiment_runs(id) ON DELETE RESTRICT,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  CHECK (concurrent_users <= creator_count),
  CHECK ((placement_manifest IS NULL) = (placement_sha256 IS NULL)),
  CHECK ((remote_hf_commit_sha IS NULL) = (remote_hf_bundle_path IS NULL)),
  CHECK (remote_hf_commit_sha IS NULL OR artifact_sha256 IS NOT NULL),
  CHECK (
    NOT population_ready OR (
      population_vector_count = target_created_babels AND
      population_vector_sha256 IS NOT NULL AND
      population_model_repository = model_repository AND
      population_model_revision = model_revision AND
      population_model_sha256 IS NOT NULL AND
      population_dataset_repository = dataset_repository AND
      population_dataset_revision = dataset_revision AND
      population_dataset_sha256 IS NOT NULL
    )
  ),
  CHECK (NOT operator_approved OR population_ready)
);

CREATE TABLE performance_conditions (
  id uuid PRIMARY KEY,
  experiment_id uuid NOT NULL REFERENCES performance_experiments(id) ON DELETE RESTRICT,
  condition_index integer NOT NULL CHECK (condition_index BETWEEN 1 AND 9),
  topology text NOT NULL CHECK (
    topology IN ('same_process', 'same_host_split', 'same_host_isolated')
  ),
  training_enabled boolean NOT NULL,
  synchronization_enabled boolean NOT NULL,
  launch_config jsonb NOT NULL,
  launch_sha256 text NOT NULL CHECK (launch_sha256 ~ '^[0-9a-f]{64}$'),
  placement_manifest jsonb,
  placement_sha256 text CHECK (placement_sha256 IS NULL OR placement_sha256 ~ '^[0-9a-f]{64}$'),
  status text NOT NULL DEFAULT 'pending' CHECK (
    status IN ('pending', 'warmup', 'running', 'draining', 'completed', 'failed')
  ),
  created_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE (experiment_id, condition_index),
  UNIQUE (experiment_id, id),
  CHECK ((placement_manifest IS NULL) = (placement_sha256 IS NULL))
);

CREATE TABLE performance_progress_snapshots (
  experiment_id uuid NOT NULL REFERENCES performance_experiments(id) ON DELETE RESTRICT,
  sequence bigint NOT NULL CHECK (sequence >= 0),
  phase text NOT NULL,
  condition_index integer CHECK (condition_index BETWEEN 1 AND 9),
  condition_count integer NOT NULL DEFAULT 9 CHECK (condition_count BETWEEN 1 AND 9),
  seeded_articles bigint NOT NULL DEFAULT 0 CHECK (seeded_articles >= 0),
  created_babels bigint NOT NULL DEFAULT 0 CHECK (created_babels >= 0),
  indexed_babels bigint NOT NULL DEFAULT 0 CHECK (indexed_babels >= 0),
  requested bigint NOT NULL DEFAULT 0 CHECK (requested >= 0),
  completed bigint NOT NULL DEFAULT 0 CHECK (completed >= 0),
  elapsed_seconds double precision NOT NULL DEFAULT 0 CHECK (elapsed_seconds >= 0),
  recent_rate double precision NOT NULL DEFAULT 0 CHECK (recent_rate >= 0),
  draining boolean NOT NULL DEFAULT false,
  telemetry jsonb NOT NULL DEFAULT '{}'::jsonb,
  captured_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (experiment_id, sequence)
);

CREATE TABLE performance_results (
  experiment_id uuid NOT NULL REFERENCES performance_experiments(id) ON DELETE RESTRICT,
  condition_id uuid NOT NULL,
  raw_evidence jsonb NOT NULL,
  evidence_sha256 text NOT NULL CHECK (evidence_sha256 ~ '^[0-9a-f]{64}$'),
  serving_p95_ms double precision NOT NULL CHECK (serving_p95_ms >= 0),
  training_p95_ms double precision CHECK (training_p95_ms >= 0),
  full_p95_ms double precision CHECK (full_p95_ms >= 0),
  itraining double precision,
  ifull double precision,
  iactivation_increment double precision,
  created_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (experiment_id, condition_id),
  FOREIGN KEY (experiment_id, condition_id)
    REFERENCES performance_conditions(experiment_id, id) ON DELETE RESTRICT,
  CHECK (
    (itraining IS NULL AND ifull IS NULL AND iactivation_increment IS NULL) OR
    (itraining IS NOT NULL AND ifull IS NOT NULL AND iactivation_increment IS NOT NULL AND
     training_p95_ms IS NOT NULL AND full_p95_ms IS NOT NULL AND
     serving_p95_ms > 0 AND training_p95_ms > 0 AND
     abs(itraining - training_p95_ms / serving_p95_ms) < 0.000000001 AND
     abs(ifull - full_p95_ms / serving_p95_ms) < 0.000000001 AND
     abs(iactivation_increment - full_p95_ms / training_p95_ms) < 0.000000001)
  )
);

CREATE TABLE performance_approvals (
  experiment_id uuid NOT NULL REFERENCES performance_experiments(id) ON DELETE RESTRICT,
  approval_sequence bigint NOT NULL CHECK (approval_sequence > 0),
  action text NOT NULL CHECK (action IN ('start_matrix', 'approve_next_scale')),
  population_vector_count bigint NOT NULL,
  population_vector_sha256 text NOT NULL CHECK (population_vector_sha256 ~ '^[0-9a-f]{64}$'),
  approved_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (experiment_id, approval_sequence)
);

CREATE FUNCTION prevent_performance_identity_mutation()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF NEW.topology IS DISTINCT FROM OLD.topology OR
     NEW.starting_model_id IS DISTINCT FROM OLD.starting_model_id OR
     NEW.model_repository IS DISTINCT FROM OLD.model_repository OR
     NEW.model_revision IS DISTINCT FROM OLD.model_revision OR
     NEW.dataset_repository IS DISTINCT FROM OLD.dataset_repository OR
     NEW.dataset_revision IS DISTINCT FROM OLD.dataset_revision OR
     NEW.retrieval_backend IS DISTINCT FROM OLD.retrieval_backend OR
     NEW.creator_count IS DISTINCT FROM OLD.creator_count OR
     NEW.seeded_articles IS DISTINCT FROM OLD.seeded_articles OR
     NEW.target_created_babels IS DISTINCT FROM OLD.target_created_babels OR
     NEW.concurrent_users IS DISTINCT FROM OLD.concurrent_users OR
     NEW.recommendation_start_probability IS DISTINCT FROM OLD.recommendation_start_probability OR
     NEW.continuation_probability IS DISTINCT FROM OLD.continuation_probability OR
     NEW.maximum_traversal_depth IS DISTINCT FROM OLD.maximum_traversal_depth OR
     NEW.maximum_requests_per_traversal IS DISTINCT FROM OLD.maximum_requests_per_traversal OR
     NEW.training_micro_batch_size IS DISTINCT FROM OLD.training_micro_batch_size OR
     NEW.sync_every_steps IS DISTINCT FROM OLD.sync_every_steps OR
     NEW.interleave_creation_and_recommendations IS DISTINCT FROM OLD.interleave_creation_and_recommendations OR
     NEW.auto_advance IS DISTINCT FROM OLD.auto_advance OR
     NEW.warmup_seconds IS DISTINCT FROM OLD.warmup_seconds OR
     NEW.duration_seconds IS DISTINCT FROM OLD.duration_seconds OR
     NEW.target_rps IS DISTINCT FROM OLD.target_rps OR
     NEW.latency_safety_threshold_ms IS DISTINCT FROM OLD.latency_safety_threshold_ms THEN
    RAISE EXCEPTION 'performance experiment identity is immutable';
  END IF;
  IF NEW.operator_approved AND NOT NEW.population_ready THEN
    RAISE EXCEPTION 'population-ready evidence is required before operator approval';
  END IF;
  IF OLD.population_ready AND (
     NEW.population_ready IS DISTINCT FROM OLD.population_ready OR
     NEW.population_vector_count IS DISTINCT FROM OLD.population_vector_count OR
     NEW.population_vector_sha256 IS DISTINCT FROM OLD.population_vector_sha256 OR
     NEW.population_model_repository IS DISTINCT FROM OLD.population_model_repository OR
     NEW.population_model_revision IS DISTINCT FROM OLD.population_model_revision OR
     NEW.population_model_sha256 IS DISTINCT FROM OLD.population_model_sha256 OR
     NEW.population_dataset_repository IS DISTINCT FROM OLD.population_dataset_repository OR
     NEW.population_dataset_revision IS DISTINCT FROM OLD.population_dataset_revision OR
     NEW.population_dataset_sha256 IS DISTINCT FROM OLD.population_dataset_sha256) THEN
    RAISE EXCEPTION 'population-ready evidence is immutable';
  END IF;
  IF OLD.operator_approved AND NOT NEW.operator_approved THEN
    RAISE EXCEPTION 'operator approval is immutable';
  END IF;
  IF OLD.placement_sha256 IS NOT NULL AND (
     NEW.placement_manifest IS DISTINCT FROM OLD.placement_manifest OR
     NEW.placement_sha256 IS DISTINCT FROM OLD.placement_sha256) THEN
    RAISE EXCEPTION 'verified placement is immutable';
  END IF;
  IF OLD.hardware_identity <> '{}'::jsonb AND
     NEW.hardware_identity IS DISTINCT FROM OLD.hardware_identity THEN
    RAISE EXCEPTION 'hardware identity is immutable';
  END IF;
  IF OLD.resource_identity <> '{}'::jsonb AND
     NEW.resource_identity IS DISTINCT FROM OLD.resource_identity THEN
    RAISE EXCEPTION 'resource identity is immutable';
  END IF;
  IF OLD.request_identity <> '{}'::jsonb AND
     NEW.request_identity IS DISTINCT FROM OLD.request_identity THEN
    RAISE EXCEPTION 'request identity is immutable';
  END IF;
  IF OLD.feedback_identity <> '{}'::jsonb AND
     NEW.feedback_identity IS DISTINCT FROM OLD.feedback_identity THEN
    RAISE EXCEPTION 'feedback identity is immutable';
  END IF;
  IF OLD.remote_hf_commit_sha IS NOT NULL AND (
     NEW.artifact_sha256 IS DISTINCT FROM OLD.artifact_sha256 OR
     NEW.remote_hf_commit_sha IS DISTINCT FROM OLD.remote_hf_commit_sha OR
     NEW.remote_hf_bundle_path IS DISTINCT FROM OLD.remote_hf_bundle_path) THEN
    RAISE EXCEPTION 'verified remote artifact is immutable';
  END IF;
  IF OLD.safety_receipt IS NOT NULL AND
     NEW.safety_receipt IS DISTINCT FROM OLD.safety_receipt THEN
    RAISE EXCEPTION 'safety receipt is immutable';
  END IF;
  NEW.updated_at := now();
  RETURN NEW;
END;
$$;

CREATE TRIGGER performance_experiment_identity_immutable
BEFORE UPDATE ON performance_experiments
FOR EACH ROW EXECUTE FUNCTION prevent_performance_identity_mutation();

CREATE FUNCTION prevent_performance_row_mutation()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  RAISE EXCEPTION 'saved performance evidence is immutable';
END;
$$;

CREATE FUNCTION prevent_performance_condition_identity_mutation()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF NEW.experiment_id IS DISTINCT FROM OLD.experiment_id OR
     NEW.condition_index IS DISTINCT FROM OLD.condition_index OR
     NEW.topology IS DISTINCT FROM OLD.topology OR
     NEW.training_enabled IS DISTINCT FROM OLD.training_enabled OR
     NEW.synchronization_enabled IS DISTINCT FROM OLD.synchronization_enabled OR
     NEW.launch_config IS DISTINCT FROM OLD.launch_config OR
     NEW.launch_sha256 IS DISTINCT FROM OLD.launch_sha256 OR
     (OLD.placement_sha256 IS NOT NULL AND (
       NEW.placement_manifest IS DISTINCT FROM OLD.placement_manifest OR
       NEW.placement_sha256 IS DISTINCT FROM OLD.placement_sha256)) THEN
    RAISE EXCEPTION 'performance condition identity is immutable';
  END IF;
  RETURN NEW;
END;
$$;

CREATE TRIGGER performance_conditions_identity_immutable
BEFORE UPDATE ON performance_conditions
FOR EACH ROW EXECUTE FUNCTION prevent_performance_condition_identity_mutation();

CREATE TRIGGER performance_conditions_delete_immutable
BEFORE DELETE ON performance_conditions
FOR EACH ROW EXECUTE FUNCTION prevent_performance_row_mutation();

CREATE TRIGGER performance_progress_immutable
BEFORE UPDATE OR DELETE ON performance_progress_snapshots
FOR EACH ROW EXECUTE FUNCTION prevent_performance_row_mutation();

CREATE TRIGGER performance_results_immutable
BEFORE UPDATE OR DELETE ON performance_results
FOR EACH ROW EXECUTE FUNCTION prevent_performance_row_mutation();

CREATE TRIGGER performance_approvals_immutable
BEFORE UPDATE OR DELETE ON performance_approvals
FOR EACH ROW EXECUTE FUNCTION prevent_performance_row_mutation();

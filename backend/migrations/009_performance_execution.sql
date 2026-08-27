ALTER TABLE performance_experiments
  ADD COLUMN population_manifest_sha256 text CHECK (
    population_manifest_sha256 IS NULL OR
    population_manifest_sha256 ~ '^[0-9a-f]{64}$'
  ),
  ADD COLUMN population_bundle_path text,
  ADD COLUMN failure text,
  ADD CONSTRAINT performance_population_execution_binding_complete CHECK (
    (run_id IS NULL AND population_manifest_sha256 IS NULL AND
     population_bundle_path IS NULL) OR
    (run_id IS NOT NULL AND population_manifest_sha256 IS NOT NULL AND
     population_bundle_path IS NOT NULL AND char_length(population_bundle_path) > 0)
  );

ALTER TABLE performance_conditions
  ADD COLUMN run_id uuid REFERENCES experiment_runs(id) ON DELETE RESTRICT;

CREATE UNIQUE INDEX performance_conditions_run_id_unique
  ON performance_conditions (run_id)
  WHERE run_id IS NOT NULL;

CREATE FUNCTION prevent_performance_execution_binding_mutation()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF OLD.run_id IS NOT NULL AND (
     NEW.run_id IS DISTINCT FROM OLD.run_id OR
     NEW.population_manifest_sha256 IS DISTINCT FROM OLD.population_manifest_sha256 OR
     NEW.population_bundle_path IS DISTINCT FROM OLD.population_bundle_path) THEN
    RAISE EXCEPTION 'performance population execution binding is immutable';
  END IF;
  RETURN NEW;
END;
$$;

CREATE TRIGGER performance_execution_binding_immutable
BEFORE UPDATE ON performance_experiments
FOR EACH ROW EXECUTE FUNCTION prevent_performance_execution_binding_mutation();

CREATE FUNCTION prevent_performance_condition_run_mutation()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF OLD.run_id IS NOT NULL AND NEW.run_id IS DISTINCT FROM OLD.run_id THEN
    RAISE EXCEPTION 'performance condition run identity is immutable';
  END IF;
  RETURN NEW;
END;
$$;

CREATE TRIGGER performance_condition_run_immutable
BEFORE UPDATE ON performance_conditions
FOR EACH ROW EXECUTE FUNCTION prevent_performance_condition_run_mutation();

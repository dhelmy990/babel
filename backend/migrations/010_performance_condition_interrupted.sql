ALTER TABLE performance_conditions
  DROP CONSTRAINT performance_conditions_status_check,
  ADD CONSTRAINT performance_conditions_status_check CHECK (
    status IN (
      'pending', 'warmup', 'running', 'draining',
      'completed', 'failed', 'interrupted'
    )
  );

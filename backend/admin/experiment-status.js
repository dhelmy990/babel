(function attachExperimentStatus(root, factory) {
  const api = factory();
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  root.BabelExperimentStatus = api;
}(typeof globalThis !== 'undefined' ? globalThis : this, function createExperimentStatus() {
  const ACTIVE_STATES = new Set([
    'starting', 'running', 'stop_requested', 'draining_feedback',
    'checkpointing', 'exporting_interactions',
  ]);

  function launchDefaults() {
    return {
      retrievalBackend: 'pgvector',
      creatorCount: 50,
      scenario: 'june_to_july',
      eventBudgetPerMonth: 100,
      runSeed: 0,
    };
  }

  function isActiveExperiment(run) {
    return Boolean(run && ACTIVE_STATES.has(run.status));
  }

  function launchView(run) {
    const defaults = launchDefaults();
    return {
      retrievalBackend: run?.retrievalBackend || defaults.retrievalBackend,
      creatorCount: Number.isInteger(run?.creatorCount) ? run.creatorCount : defaults.creatorCount,
      scenario: Array.isArray(run?.environmentSequence) && run.environmentSequence.length === 1
        ? 'june_only' : defaults.scenario,
      disabled: isActiveExperiment(run),
    };
  }

  function modelOptions(models) {
    if (!Array.isArray(models)) return [];
    return models.map((model) => ({
      value: String(model.modelId || ''),
      label: `${model.label || 'Unnamed model'} · ${model.parentModelId ? 'child' : 'original'}`,
      disabled: model.immutable !== true || model.compatible === false,
      immutable: model.immutable === true,
      reason: model.incompatibilityReason || (model.immutable === true ? '' : 'model is not immutable'),
    }));
  }

  function number(value, fallback = 0) {
    return Number.isFinite(value) ? value : fallback;
  }

  function healthView(run) {
    if (!run || typeof run !== 'object') return [];
    const checkpoint = run.checkpointPath || 'pending';
    const rankLoss = run.rollingRankLoss == null ? 'pending' : number(run.rollingRankLoss);
    const rows = [
      ['Created Babels', String(number(run.createdBabelCount))],
      ['Feedback', String(number(run.feedbackCount))],
      ['Event rate', `${number(run.eventRate)}/s`],
      ['Kafka', `offset ${number(run.kafkaOffset)} · lag ${number(run.kafkaLag)}`],
      ['Trainer', `step ${number(run.trainerSteps)} · rank loss ${rankLoss}`],
      ['Checkpoint', checkpoint],
      ['Serving sync', `${run.servingSynced ? 'synced' : 'pending'} · model v${number(run.activeModelVersion)}`],
    ];
    if (run.placement && typeof run.placement === 'object') {
      rows.push(['Placement', `${run.placement.topology || 'unknown'} · serving ${run.placement.servingPid ?? 'pending'} · trainer ${run.placement.trainerPid ?? 'pending'}`]);
    }
    if (run.sourceVectorOrigins && typeof run.sourceVectorOrigins === 'object') {
      rows.push(['Vector origins', `qwen ${number(run.sourceVectorOrigins.qwen_encode)} · cache ${number(run.sourceVectorOrigins.cache_hit)} · pgvector ${number(run.sourceVectorOrigins.pgvector_load)}`]);
    }
    if (Number.isFinite(run.trainerServingStalenessSteps)) {
      rows.push(['Model staleness', `${run.trainerServingStalenessSteps} trainer steps`]);
    }
    return rows;
  }

  function joined(values) {
    return Array.isArray(values) && values.length ? values.join(', ') : 'none';
  }

  function activityView(activity) {
    const row = activity && typeof activity === 'object' ? activity : {};
    const details = row.details && typeof row.details === 'object' ? row.details : { kind: 'lifecycle' };
    let summary = String(row.message || row.event || 'Experiment activity');
    if (details.kind === 'recommendation') {
      const edges = number(details.acceptedEdgeCount);
      summary = `${details.creatorId} · ${details.newBabelTitle} (${details.newBabelId}) · `
        + `candidates ${joined(details.candidateBabelIds)} · include ${joined(details.includeBabelIds)} · `
        + `exclude ${joined(details.excludeBabelIds)} · ignore ${joined(details.ignoreBabelIds)} · `
        + `${edges} accepted edge${edges === 1 ? '' : 's'} · ${details.modelId} v${number(details.modelVersion)}`;
      if (details.requestId) summary += ` · request ${details.requestId}`;
      if (details.traversalSessionId) summary += ` · walk ${details.traversalSessionId}`;
      if (details.sourceVectorOrigin) summary += ` · vector ${details.sourceVectorOrigin}`;
    } else if (details.kind === 'feedback') {
      summary = `${summary} · offset ${number(details.kafkaOffset)} · lag ${number(details.kafkaLag)}`;
    } else if (details.kind === 'training') {
      summary = `${summary} · step ${number(details.trainerStep)} · rolling rank loss ${number(details.rollingRankLoss)}`;
    } else if (details.kind === 'synchronization') {
      summary = `${summary} · ${details.checkpointPath} · sync v${number(details.synchronizationVersion)} · ${details.modelId} v${number(details.modelVersion)}`;
    }
    return {
      sequence: number(row.sequence),
      occurredAtNs: number(row.occurredAtNs),
      level: String(row.level || 'info'),
      component: String(row.component || 'supervisor'),
      event: String(row.event || ''),
      summary,
    };
  }

  return { activityView, healthView, isActiveExperiment, launchDefaults, launchView, modelOptions };
}));

(function attachScalabilityDashboard(root, factory) {
  const api = factory(root, root.BabelTrialProgress);
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  root.BabelScalabilityDashboard = api;
}(typeof globalThis !== 'undefined' ? globalThis : this, function createScalabilityDashboard(root, progressApi) {
  const MODEL_REPOSITORY = 'dhelmy990/babel-qwen-navigation-2016-interview';
  const MODEL_REVISION = '57d949cd634b920cc1a46f27c9b21df094b5240e';
  const DATASET_REPOSITORY = 'dhelmy990/babel-wikipedia-experiment';
  const DATASET_REVISION = '0d1ab2c7f0e2295682288fcf10077d2d776bf559';

  function trialDefaults() {
    return {
      topology: 'same_host_split',
      modelRepository: MODEL_REPOSITORY,
      modelRevision: MODEL_REVISION,
      datasetRepository: DATASET_REPOSITORY,
      datasetRevision: DATASET_REVISION,
      retrievalBackend: 'pgvector', creatorCount: 50,
      seededArticles: 10000, targetCreatedBabels: 10000, concurrentUsers: 50,
      recommendationStartProbability: 0.40, continuationProbability: 0.40,
      maximumTraversalDepth: 2, maximumRequestsPerTraversal: 10,
      trainingMicroBatchSize: 8, syncEverySteps: 10,
      interleaveCreationAndRecommendations: true, autoAdvance: false,
    };
  }

  function bindPairedControl(slider, numeric) {
    slider.addEventListener('input', () => { numeric.value = slider.value; });
    numeric.addEventListener('input', () => {
      const parsed = Number(numeric.value);
      if (!Number.isFinite(parsed)) return;
      slider.value = String(Math.max(Number(slider.min), Math.min(Number(slider.max), parsed)));
    });
  }

  function ratio(numerator, denominator) {
    return Number.isFinite(numerator) && Number.isFinite(denominator) && denominator > 0
      ? numerator / denominator : null;
  }

  function interferenceRatios(serving, training, full) {
    return {
      Itraining: ratio(training, serving),
      Ifull: ratio(full, serving),
      IActivationIncrement: ratio(full, training),
    };
  }

  function populationGateView(trial) {
    const evidence = trial?.populationEvidence || {};
    const digest = (value) => /^[0-9a-f]{64}$/.test(String(value || ''));
    const ready = trial?.populationReady === true
      && Number(evidence.vectorCount) === Number(evidence.requiredVectorCount)
      && Number(evidence.vectorCount) === Number(trial?.requiredVectorCount)
      && digest(evidence.vectorSha256) && digest(evidence.modelSha256)
      && digest(evidence.datasetSha256)
      && evidence.modelRepository === trial?.modelRepository
      && evidence.modelRevision === trial?.modelRevision
      && evidence.datasetRepository === trial?.datasetRepository
      && evidence.datasetRevision === trial?.datasetRevision;
    const approved = trial?.operatorApproved === true;
    const formal = trial?.requestIdentity?.evidenceScope == null
      || trial.requestIdentity.evidenceScope === 'formal';
    return {
      canRunFormalTrial: ready && approved && formal,
      message: !ready ? 'Waiting for exact real model, dataset, vector count, and checksum evidence.'
        : approved && formal ? 'Population approved for formal measurements.'
          : approved ? 'Population approved for representative measurements (non-formal).'
          : 'Population ready; explicit operator approval is required before measurements.',
    };
  }

  function buildTrialRequest(values = {}) {
    return { ...trialDefaults(), ...values, autoAdvance: false };
  }

  function cohortConditionCount(cohortSize) {
    const size = Number(cohortSize);
    if (![50, 100, 500].includes(size)) {
      throw new Error('Formal cohort must be 50, 100, or 500 creators');
    }
    return size === 50 ? 9 : 6;
  }

  function cohortTrialRequest(cohortSize, values = {}) {
    const size = Number(cohortSize);
    cohortConditionCount(size);
    return buildTrialRequest({
      ...values,
      topology: 'same_host_split',
      creatorCount: size,
      concurrentUsers: size,
      seededArticles: 10000,
      targetCreatedBabels: 10000,
      autoAdvance: false,
    });
  }

  function evidenceScope(trial) {
    const scope = trial?.requestIdentity?.evidenceScope;
    if (scope === 'representative_same_process_vs_split') {
      return { label: 'representative · non-formal 2×3', conditionCount: 6 };
    }
    if (scope === 'representative_split_smoke') {
      return { label: 'representative · non-formal split smoke', conditionCount: 3 };
    }
    if (scope === 'representative_isolated_smoke') {
      return { label: 'representative · non-formal isolated smoke', conditionCount: 3 };
    }
    return { label: 'formal', conditionCount: cohortConditionCount(trial?.creatorCount || 50) };
  }

  async function jsonRequest(fetchImpl, url, options = {}) {
    let response;
    try {
      response = await fetchImpl(url, {
        ...options, headers: { Accept: 'application/json', ...(options.headers || {}) },
      });
    } catch (error) {
      throw new Error(`Unable to reach performance control: ${error.message || 'network error'}`);
    }
    const payload = await response.json().catch(() => null);
    if (!response.ok) {
      throw new Error(payload?.error?.message || `Performance request failed (${response.status})`);
    }
    return payload;
  }

  function createScalabilityController(options) {
    const documentRef = options.document;
    const fetchImpl = options.fetchImpl || root.fetch.bind(root);
    const progressApiImpl = options.progressApi || progressApi;
    const setIntervalImpl = options.setIntervalImpl || root.setInterval.bind(root);
    const clearIntervalImpl = options.clearIntervalImpl || root.clearInterval.bind(root);
    const uuidImpl = options.uuidImpl || (() => root.crypto.randomUUID());
    if (!progressApiImpl?.createTrialProgressPoller || !progressApiImpl?.progressView) {
      throw new Error('The read-only trial progress component is unavailable');
    }
    const ids = [
      'performance-status', 'performance-cohort', 'performance-topology', 'performance-model',
      'performance-backend', 'performance-dataset', 'performance-creators', 'performance-seeded-slider',
      'performance-seeded', 'performance-created-slider', 'performance-created',
      'performance-concurrent-slider', 'performance-concurrent',
      'performance-start-probability', 'performance-continuation-probability',
      'performance-depth', 'performance-request-cap',
      'performance-warmup', 'performance-duration', 'performance-rps',
      'performance-safety', 'performance-training-batch', 'performance-sync-steps',
      'performance-interleave', 'performance-create',
      'performance-rerun-source', 'performance-rerun-matrix',
      'performance-rerun-warmup', 'performance-rerun-duration',
      'performance-rerun-rps', 'performance-rerun',
      'performance-approve', 'performance-stop', 'performance-phase',
      'performance-progress', 'performance-rate', 'performance-gate',
      'performance-ratios', 'performance-results', 'performance-telemetry',
      'performance-trials',
    ];
    const elements = Object.fromEntries(ids.map((id) => [id, documentRef.getElementById(id)]));
    const nonce = documentRef.querySelector('meta[name="babel-admin-nonce"]')?.content || '';
    const models = new Map();
    let current = null;
    let pending = false;
    let timer = null;

    bindPairedControl(elements['performance-seeded-slider'], elements['performance-seeded']);
    bindPairedControl(elements['performance-created-slider'], elements['performance-created']);
    bindPairedControl(elements['performance-concurrent-slider'], elements['performance-concurrent']);
    function synchronizeCohortControls() {
      const requested = Number(elements['performance-cohort'].value || 50);
      const size = [50, 100, 500].includes(requested) ? requested : 50;
      elements['performance-creators'].value = String(size);
      elements['performance-concurrent'].value = String(size);
      elements['performance-concurrent-slider'].value = String(size);
      elements['performance-seeded'].value = '10000';
      elements['performance-seeded-slider'].value = '10000';
      elements['performance-created'].value = '10000';
      elements['performance-created-slider'].value = '10000';
      elements['performance-topology'].value = 'same_host_split';
      elements['performance-topology'].disabled = size > 50;
      for (const id of [
        'performance-creators', 'performance-concurrent',
        'performance-concurrent-slider', 'performance-seeded',
        'performance-seeded-slider', 'performance-created',
        'performance-created-slider',
      ]) elements[id].disabled = true;
    }
    elements['performance-cohort'].addEventListener('change', synchronizeCohortControls);
    synchronizeCohortControls();

    function setStatus(message, error = false) {
      elements['performance-status'].textContent = message;
      elements['performance-status'].classList?.toggle('is-error', error);
    }

    function definitionList(element, rows) {
      element.replaceChildren();
      for (const [label, value] of rows) {
        const item = documentRef.createElement('div');
        const term = documentRef.createElement('dt');
        const description = documentRef.createElement('dd');
        term.textContent = label;
        if (value?.kind === 'link') {
          const link = documentRef.createElement('a');
          link.href = value.href;
          link.textContent = value.label;
          link.target = '_blank';
          link.rel = 'noopener noreferrer';
          description.appendChild(link);
        } else {
          description.textContent = value == null ? 'pending' : String(value);
        }
        item.appendChild(term); item.appendChild(description); element.appendChild(item);
      }
    }

    function display(value) {
      if (value == null || value === '') return 'pending';
      return typeof value === 'object' ? JSON.stringify(value) : String(value);
    }

    function artifactLink(trial) {
      const repository = String(trial.datasetRepository || '');
      const commit = String(trial.remoteHfCommitSha || '');
      const rawPath = String(trial.remoteHfBundlePath || '');
      if (!/^[A-Za-z0-9_.-]+\/[A-Za-z0-9_.-]+$/.test(repository)
          || !/^[0-9a-f]{40,64}$/.test(commit) || !rawPath) return null;
      const segments = rawPath.split('/').filter((segment) => segment && segment !== '.' && segment !== '..');
      if (!segments.length) return null;
      const path = segments.map((segment) => encodeURIComponent(segment)).join('/');
      return {
        kind: 'link',
        href: `https://huggingface.co/datasets/${repository}/tree/${commit}/${path}`,
        label: `${commit.slice(0, 12)} · ${rawPath}`,
      };
    }

    function renderProgress(view) {
      elements['performance-phase'].textContent = `${view.phase} · condition ${view.condition} · seeded ${view.seeded} · created ${view.created} · indexed ${view.indexed}`;
      elements['performance-progress'].value = view.percent;
      elements['performance-progress'].textContent = `${view.percent}%`;
      elements['performance-rate'].textContent = `${view.submitted}/${view.requested} submitted · ${view.completed} completed · ${view.errors} errors · ${view.inFlight} in flight · ${view.rate} · elapsed ${view.elapsed} · ETA ${view.eta}${view.draining ? ' · draining' : ''}`;
    }

    function renderTrial(trial, persistedProgressView = null) {
      current = trial;
      if (!trial) {
        setStatus('No scalability trial has been saved');
        return;
      }
      const scope = evidenceScope(trial);
      setStatus(`${String(trial.status).replaceAll('_', ' ')} · ${scope.label} · ${trial.creatorCount || 50} creators · ${trial.progress?.conditionCount || scope.conditionCount} conditions · ${trial.targetCreatedBabels} Babel target`);
      elements['performance-rerun-source'].textContent = trial.experimentId;
      const gate = populationGateView(trial);
      elements['performance-gate'].textContent = gate.message;
      elements['performance-approve'].disabled = pending || !trial.populationReady
        || trial.status !== 'population_ready';
      elements['performance-stop'].disabled = pending || ![
        'population_pending', 'population_ready', 'approved', 'running', 'stop_requested',
      ].includes(trial.status);
      elements['performance-create'].disabled = pending || ['running', 'draining'].includes(trial.status);
      elements['performance-rerun'].disabled = pending || trial.populationReady !== true;
      if (persistedProgressView) renderProgress(persistedProgressView);
      else if (trial.progress) renderProgress(progressApiImpl.progressView(trial.progress));
      const telemetry = trial.progress?.telemetry || {};
      const latestResult = Array.isArray(trial.results) && trial.results.length
        ? trial.results[trial.results.length - 1] : null;
      const calculated = interferenceRatios(
        Number(telemetry.servingP95Ms), Number(telemetry.trainingP95Ms),
        Number(telemetry.fullP95Ms));
      const ratios = latestResult ? {
        Itraining: latestResult.Itraining,
        Ifull: latestResult.Ifull,
        IActivationIncrement: latestResult.IActivationIncrement,
      } : calculated;
      definitionList(elements['performance-ratios'], [
        ['Itraining = T/S', ratios.Itraining],
        ['Ifull = F/S', ratios.Ifull],
        ['IActivationIncrement = F/T', ratios.IActivationIncrement],
      ]);
      elements['performance-results'].replaceChildren();
      const conditionCount = trial.progress?.conditionCount
        || scope.conditionCount;
      const results = Array.isArray(trial.results)
        ? [...trial.results].sort((left, right) => (
          Number(left.conditionIndex) - Number(right.conditionIndex)))
        : [];
      for (const result of results) {
        const row = documentRef.createElement('li');
        const mode = !result.trainingEnabled ? 'serving only'
          : result.synchronizationEnabled ? 'training + activation'
            : 'training, no activation';
        const conditionP95 = result.rawEvidence?.conditionP95Ms
          ?? result.servingP95Ms;
        row.textContent = `${result.conditionIndex}/${conditionCount}`
          + ` · ${result.topology} · ${mode} · p95 ${display(conditionP95)} ms`
          + ` · Itraining ${display(result.Itraining)} · Ifull ${display(result.Ifull)}`
          + ` · IActivationIncrement ${display(result.IActivationIncrement)}`;
        elements['performance-results'].appendChild(row);
      }
      definitionList(elements['performance-telemetry'], [
        ['Topology / placement', `${trial.topology} · ${display(trial.placement)}`],
        ['PIDs / resources', `${display(trial.placement?.processes)} · ${display(trial.resources)}`],
        ['Request latency stages', display(telemetry.requestLatencyStages)],
        ['Events / actions / edges', display(telemetry.eventCounts)],
        ['Traversal starts / continuation rolls', `starts ${display(telemetry.traversalStarts)} · rolls ${display(telemetry.continuationRolls)}`],
        ['Walk depth / cap outcomes', `depth ${display(telemetry.depthOutcomes)} · cap ${display(telemetry.capOutcomes)}`],
        ['Walk requests / walks', `requests ${display(telemetry.walkRequestCount)} · walks ${display(telemetry.walkCount)} · requests/walk ${display(telemetry.requestsPerWalk)}`],
        ['Kafka', `lag ${display(telemetry.kafkaLag)} · offsets ${display(telemetry.kafkaOffsets)}`],
        ['Trainer', `steps ${display(telemetry.trainerSteps)} · loss ${display(telemetry.rollingRankLoss)}`],
        ['Vector cache origins', display(telemetry.sourceVectorOrigins)],
        ['Checkpoint / sync', `${display(telemetry.checkpoint)} · ${display(telemetry.synchronization)}`],
        ['Model staleness', display(telemetry.modelStaleness)],
        ['Activation spikes', display(telemetry.activationSpikes)],
        ['Artifact', artifactLink(trial) || 'local evidence pending upload'],
        ['Run activity log', trial.runId ? `/admin/api/v1/experiment/runs/${trial.runId}/logs` : 'pending'],
      ]);
    }

    const progressPoller = progressApiImpl.createTrialProgressPoller({
      fetchImpl,
      render(view, trial) {
        if (!trial || trial.experimentId !== current?.experimentId) return;
        renderTrial(trial, view);
      },
    });

    function renderSaved(trials) {
      elements['performance-trials'].replaceChildren();
      for (const trial of trials) {
        const item = documentRef.createElement('li');
        const heading = documentRef.createElement('strong');
        const body = documentRef.createElement('button');
        heading.textContent = trial.createdAt || trial.experimentId;
        body.type = 'button'; body.className = 'secondary';
        const scope = evidenceScope(trial);
        body.textContent = `${trial.status} · ${scope.label} · ${trial.creatorCount || 50} creators · ${trial.progress?.conditionCount || scope.conditionCount} conditions`;
        body.addEventListener('click', () => loadTrial(trial.experimentId));
        item.appendChild(heading); item.appendChild(body);
        elements['performance-trials'].appendChild(item);
      }
    }

    async function loadModels() {
      const payload = await jsonRequest(fetchImpl, '/admin/api/v1/experiment/models');
      elements['performance-model'].replaceChildren();
      for (const model of payload.models || []) {
        models.set(model.modelId, model);
        const option = documentRef.createElement('option');
        option.value = model.modelId;
        option.textContent = `${model.label} · ${model.parentModelId ? 'post-run child' : 'immutable original'}`;
        option.disabled = model.immutable !== true || model.compatible === false;
        elements['performance-model'].appendChild(option);
      }
    }

    async function loadTrial(experimentId) {
      const payload = await jsonRequest(fetchImpl,
        `/admin/api/v1/performance/${encodeURIComponent(experimentId)}`);
      renderTrial(payload.trial);
      return payload.trial;
    }

    async function loadSaved() {
      const payload = await jsonRequest(fetchImpl, '/admin/api/v1/performance?limit=25');
      renderSaved(payload.trials || []);
      if (!current && payload.trials?.length) await loadTrial(payload.trials[0].experimentId);
    }

    function launchBody() {
      const selected = models.get(elements['performance-model'].value) || {};
      return cohortTrialRequest(Number(elements['performance-cohort'].value || 50), {
        startingModelId: elements['performance-model'].value,
        topology: elements['performance-topology'].value,
        modelRepository: selected.encoderRepo || trialDefaults().modelRepository,
        modelRevision: selected.encoderRevision || trialDefaults().modelRevision,
        datasetRepository: selected.datasetRepo || trialDefaults().datasetRepository,
        datasetRevision: elements['performance-dataset'].value
          || selected.datasetRevision || trialDefaults().datasetRevision,
        retrievalBackend: elements['performance-backend'].value,
        creatorCount: Number(elements['performance-creators'].value),
        seededArticles: Number(elements['performance-seeded'].value),
        targetCreatedBabels: Number(elements['performance-created'].value),
        concurrentUsers: Number(elements['performance-concurrent'].value),
        recommendationStartProbability: Number(elements['performance-start-probability'].value),
        continuationProbability: Number(elements['performance-continuation-probability'].value),
        maximumTraversalDepth: Number(elements['performance-depth'].value),
        maximumRequestsPerTraversal: Number(elements['performance-request-cap'].value),
        trainingMicroBatchSize: Number(elements['performance-training-batch'].value),
        syncEverySteps: Number(elements['performance-sync-steps'].value),
        warmupSeconds: Number(elements['performance-warmup'].value),
        durationSeconds: Number(elements['performance-duration'].value),
        targetRps: Number(elements['performance-rps'].value),
        latencySafetyThresholdMs: Number(elements['performance-safety'].value),
        interleaveCreationAndRecommendations: elements['performance-interleave'].checked,
      });
    }

    async function mutation(path, body) {
      pending = true;
      try {
        const payload = await jsonRequest(fetchImpl, path, {
          method: 'POST',
          headers: { 'X-Babel-Admin-Nonce': nonce, ...(body ? { 'Content-Type': 'application/json' } : {}) },
          ...(body ? { body: JSON.stringify(body) } : {}),
        });
        renderTrial(payload.trial);
        await loadSaved();
      } catch (error) {
        setStatus(error.message, true);
      } finally {
        pending = false;
        if (current) renderTrial(current);
      }
    }

    elements['performance-create'].addEventListener('click', () => mutation('/admin/api/v1/performance', launchBody()));
    elements['performance-rerun'].addEventListener('click', () => current && mutation(
      `/admin/api/v1/performance/${encodeURIComponent(current.experimentId)}/representative-rerun`,
      {
        rerunId: uuidImpl(),
        matrix: elements['performance-rerun-matrix'].value || '2x3',
        warmupSeconds: Number(elements['performance-rerun-warmup'].value),
        durationSeconds: Number(elements['performance-rerun-duration'].value),
        targetRps: Number(elements['performance-rerun-rps'].value),
      },
    ));
    elements['performance-approve'].addEventListener('click', () => current && mutation(`/admin/api/v1/performance/${encodeURIComponent(current.experimentId)}/approve-next-scale`));
    elements['performance-stop'].addEventListener('click', () => current && mutation(`/admin/api/v1/performance/${encodeURIComponent(current.experimentId)}/graceful-stop`));

    async function initialize() {
      try {
        await loadModels(); await loadSaved();
        timer = setIntervalImpl(() => {
          const experimentId = current?.experimentId;
          if (!experimentId) return Promise.resolve();
          return progressPoller.poll(experimentId).catch(() => {
            if (current?.experimentId === experimentId) {
              elements['performance-rate'].textContent = 'Progress temporarily unavailable; trial continues independently.';
            }
          });
        }, 1000);
      } catch (error) {
        setStatus(error.message, true);
      }
    }
    function stop() { if (timer !== null) clearIntervalImpl(timer); timer = null; }
    return Object.freeze({ initialize, loadSaved, loadTrial, stop });
  }

  function initializeScalabilityDashboard() {
    const controller = createScalabilityController({ document: root.document });
    controller.initialize();
    root.addEventListener('beforeunload', controller.stop, { once: true });
    return controller;
  }

  if (root.document && root.document.readyState !== 'loading') initializeScalabilityDashboard();
  else if (root.document) root.document.addEventListener('DOMContentLoaded', initializeScalabilityDashboard, { once: true });

  return Object.freeze({
    bindPairedControl, buildTrialRequest, cohortConditionCount, cohortTrialRequest,
    createScalabilityController,
    evidenceScope, interferenceRatios, jsonRequest, populationGateView, trialDefaults,
  });
}));

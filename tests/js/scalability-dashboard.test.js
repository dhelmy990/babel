const test = require('node:test');
const assert = require('node:assert/strict');

const dashboard = require('../../backend/admin/scalability-dashboard.js');
const progress = require('../../backend/admin/trial-progress.js');

function createElement(tagName = 'div') {
  const listeners = new Map();
  return {
    tagName: tagName.toUpperCase(),
    value: '', min: '0', max: '10000', checked: false, disabled: false,
    textContent: '', children: [],
    classList: { toggle() {} },
    addEventListener(name, callback) { listeners.set(name, callback); },
    appendChild(child) { this.children.push(child); return child; },
    replaceChildren(...children) { this.children = children; },
    dispatch(name) { return listeners.get(name)?.(); },
  };
}

function createDocument() {
  const elements = new Map();
  return {
    createElement,
    getElementById(id) {
      if (!elements.has(id)) elements.set(id, createElement());
      return elements.get(id);
    },
    querySelector(selector) {
      return selector === 'meta[name="babel-admin-nonce"]' ? { content: 'nonce' } : null;
    },
  };
}

function response(payload, { ok = true, status = 200 } = {}) {
  return { ok, status, json: async () => payload };
}

function trialFixture() {
  return {
    experimentId: 'trial-1', status: 'running', topology: 'same_host_split',
    targetCreatedBabels: 10000, populationReady: true, operatorApproved: true,
    datasetRepository: 'dhelmy990/babel-wikipedia-experiment',
    remoteHfCommitSha: 'a'.repeat(40),
    remoteHfBundlePath: 'runs/trial-1/evidence bundle',
    progress: {
      phase: 'measuring', conditionIndex: 2, conditionCount: 9,
      seededArticles: 8000, targetSeededArticles: 10000,
      createdBabels: 4000, targetCreatedBabels: 10000, indexedBabels: 3900,
      requested: 1200, completed: 1150, elapsedSeconds: 60, recentRate: 20,
      telemetry: {
        traversalStarts: 320,
        continuationRolls: { attempted: 510, succeeded: 205 },
        depthOutcomes: { reachedDepthTwo: 180, depthLimit: 25 },
        capOutcomes: { requestCapReached: 7 },
        walkRequestCount: 1200, walkCount: 320, requestsPerWalk: 3.75,
      },
    },
    results: [],
  };
}

function definitionRows(element) {
  return new Map(element.children.map((row) => [
    row.children[0].textContent,
    row.children[1],
  ]));
}

async function initializeController({ failPoll = false, status = 'running' } = {}) {
  const document = createDocument();
  const calls = [];
  let detailReads = 0;
  let intervalCallback;
  const trial = trialFixture();
  trial.status = status;
  const fetchImpl = async (url, options = {}) => {
    calls.push([url, options]);
    if (url === '/admin/api/v1/experiment/models') {
      return response({ models: [{ modelId: 'model-1', label: 'Qwen', immutable: true }] });
    }
    if (url === '/admin/api/v1/performance?limit=25') {
      return response({ trials: [trial] });
    }
    if (url === '/admin/api/v1/performance/trial-1') {
      detailReads += 1;
      if (failPoll && detailReads > 1) return response({}, { ok: false, status: 503 });
      return response({ trial });
    }
    throw new Error(`unexpected request: ${url}`);
  };
  const controller = dashboard.createScalabilityController({
    document, fetchImpl, progressApi: progress,
    setIntervalImpl(callback) { intervalCallback = callback; return 17; },
    clearIntervalImpl() {},
  });
  await controller.initialize();
  return { calls, controller, document, intervalCallback, trial };
}

test('trial defaults preserve the real split Qwen experiment', () => {
  assert.deepEqual(dashboard.trialDefaults(), {
    topology: 'same_host_split',
    modelRepository: 'dhelmy990/babel-qwen-navigation-2016-interview',
    modelRevision: '57d949cd634b920cc1a46f27c9b21df094b5240e',
    datasetRepository: 'dhelmy990/babel-wikipedia-experiment',
    datasetRevision: '0d1ab2c7f0e2295682288fcf10077d2d776bf559',
    retrievalBackend: 'pgvector', creatorCount: 50,
    seededArticles: 10000, targetCreatedBabels: 10000, concurrentUsers: 50,
    recommendationStartProbability: 0.40, continuationProbability: 0.40,
    maximumTraversalDepth: 2, maximumRequestsPerTraversal: 10,
    trainingMicroBatchSize: 8, syncEverySteps: 10,
    interleaveCreationAndRecommendations: true, autoAdvance: false,
  });
});

test('100 and 500 creator cohorts save the six-condition split comparison contract', () => {
  for (const cohortSize of [100, 500]) {
    const request = dashboard.cohortTrialRequest(cohortSize, {
      topology: 'same_host_isolated', creatorCount: 7, concurrentUsers: 3,
      seededArticles: 5, targetCreatedBabels: 9, autoAdvance: true,
    });
    assert.equal(request.creatorCount, cohortSize);
    assert.equal(request.concurrentUsers, cohortSize);
    assert.equal(request.seededArticles, 10000);
    assert.equal(request.targetCreatedBabels, 10000);
    assert.equal(request.topology, 'same_host_split');
    assert.equal(request.autoAdvance, false);
    assert.equal(dashboard.cohortConditionCount(cohortSize), 6);
  }
  assert.equal(dashboard.cohortConditionCount(50), 9);
  assert.throws(() => dashboard.cohortTrialRequest(101), /50, 100, or 500/);
});

test('cohort selector synchronizes visible creator concurrency and split controls', async () => {
  const context = await initializeController();
  const cohort = context.document.getElementById('performance-cohort');
  cohort.value = '500';

  cohort.dispatch('change');

  assert.equal(context.document.getElementById('performance-creators').value, '500');
  assert.equal(context.document.getElementById('performance-concurrent').value, '500');
  assert.equal(context.document.getElementById('performance-concurrent-slider').value, '500');
  assert.equal(context.document.getElementById('performance-topology').value, 'same_host_split');
  assert.equal(context.document.getElementById('performance-seeded').value, '10000');
  assert.equal(context.document.getElementById('performance-created').value, '10000');
  assert.equal(context.document.getElementById('performance-seeded').disabled, true);
  assert.equal(context.document.getElementById('performance-created').disabled, true);
});

test('paired slider and numeric input synchronize while custom input may exceed slider max', () => {
  const listeners = {};
  const slider = { value: '50', min: '1', max: '500', addEventListener: (name, fn) => { listeners[`slider:${name}`] = fn; } };
  const numeric = { value: '50', min: '1', addEventListener: (name, fn) => { listeners[`numeric:${name}`] = fn; } };
  dashboard.bindPairedControl(slider, numeric);

  slider.value = '100'; listeners['slider:input']();
  assert.equal(numeric.value, '100');
  numeric.value = '10000'; listeners['numeric:input']();
  assert.equal(slider.value, '500');
  assert.equal(numeric.value, '10000');
});

test('dashboard names all three interference ratios explicitly', () => {
  assert.deepEqual(dashboard.interferenceRatios(10, 15, 18), {
    Itraining: 1.5, Ifull: 1.8, IActivationIncrement: 1.2,
  });
});

test('formal measurements remain gated after population reaches threshold', () => {
  const view = dashboard.populationGateView({
    status: 'population_ready', populationReady: true, operatorApproved: false,
    vectorCount: 10000, requiredVectorCount: 10000, vectorChecksum: 'a'.repeat(64),
    modelRepository: 'model/repo', modelRevision: 'b'.repeat(40),
    datasetRepository: 'dataset/repo', datasetRevision: 'c'.repeat(40),
    populationEvidence: {
      vectorCount: 10000, requiredVectorCount: 10000, vectorSha256: 'a'.repeat(64),
      modelRepository: 'model/repo', modelRevision: 'b'.repeat(40), modelSha256: 'd'.repeat(64),
      datasetRepository: 'dataset/repo', datasetRevision: 'c'.repeat(40), datasetSha256: 'e'.repeat(64),
    },
  });
  assert.equal(view.canRunFormalTrial, false);
  assert.match(view.message, /operator approval/i);
});

test('population gate rejects mismatched model or dataset provenance', () => {
  const trial = {
    populationReady: true, operatorApproved: true,
    vectorCount: 10000, requiredVectorCount: 10000, vectorChecksum: 'c'.repeat(64),
    modelRepository: 'model/repo', modelRevision: 'a'.repeat(40),
    datasetRepository: 'dataset/repo', datasetRevision: 'b'.repeat(40),
    populationEvidence: {
      vectorCount: 10000, requiredVectorCount: 10000, vectorSha256: 'c'.repeat(64),
      modelRepository: 'wrong/repo', modelRevision: 'a'.repeat(40), modelSha256: 'd'.repeat(64),
      datasetRepository: 'dataset/repo', datasetRevision: 'b'.repeat(40), datasetSha256: 'e'.repeat(64),
    },
  };
  assert.equal(dashboard.populationGateView(trial).canRunFormalTrial, false);
});

test('dashboard polling renders persisted seeded, walk, and Hugging Face evidence', async () => {
  const context = await initializeController();

  await context.intervalCallback();

  const phase = context.document.getElementById('performance-phase').textContent;
  assert.match(phase, /seeded 8000\/10000/);
  const telemetry = definitionRows(context.document.getElementById('performance-telemetry'));
  assert.equal(telemetry.get('Traversal starts / continuation rolls').textContent,
    'starts 320 · rolls {"attempted":510,"succeeded":205}');
  assert.equal(telemetry.get('Walk depth / cap outcomes').textContent,
    'depth {"reachedDepthTwo":180,"depthLimit":25} · cap {"requestCapReached":7}');
  assert.equal(telemetry.get('Walk requests / walks').textContent,
    'requests 1200 · walks 320 · requests/walk 3.75');

  const artifact = telemetry.get('Artifact');
  assert.equal(artifact.children.length, 1);
  assert.equal(artifact.children[0].tagName, 'A');
  assert.equal(artifact.children[0].href,
    `https://huggingface.co/datasets/dhelmy990/babel-wikipedia-experiment/tree/${'a'.repeat(40)}/runs/trial-1/evidence%20bundle`);
  assert.equal(artifact.children[0].target, '_blank');
  assert.equal(artifact.children[0].rel, 'noopener noreferrer');

  assert.deepEqual(context.calls.at(-1), [
    '/admin/api/v1/performance/trial-1',
    { method: 'GET', headers: { Accept: 'application/json' } },
  ]);
});

test('dashboard renders independently persisted live condition counters', async () => {
  const context = await initializeController();
  Object.assign(context.trial.progress, {
    phase: 'scheduled', conditionIndex: 4, requested: 750, completed: 300,
    elapsedSeconds: 60, recentRate: 5, draining: false,
  });
  Object.assign(context.trial.progress.telemetry, {
    conditionPhase: 'scheduled', submitted: 320, errors: 2, inFlight: 20,
  });

  await context.intervalCallback();

  assert.match(context.document.getElementById('performance-phase').textContent,
    /scheduled · condition 4\/9/);
  assert.match(context.document.getElementById('performance-rate').textContent,
    /320\/750 submitted · 300 completed · 2 errors · 20 in flight/);
  assert.equal(context.document.getElementById('performance-progress').value, 40);
});

test('dashboard renders every persisted condition result for the selected cohort', async () => {
  const context = await initializeController();
  context.trial.creatorCount = 100;
  context.trial.progress.conditionCount = 6;
  context.trial.results = [
    {
      conditionIndex: 4, topology: 'same_host_split', trainingEnabled: false,
      synchronizationEnabled: false, servingP95Ms: 18,
      Itraining: 1.5, Ifull: 1.8, IActivationIncrement: 1.2,
    },
    {
      conditionIndex: 1, topology: 'same_process', trainingEnabled: false,
      synchronizationEnabled: false, servingP95Ms: 30,
      Itraining: 1.4, Ifull: 1.7, IActivationIncrement: 1.214,
    },
  ];

  await context.intervalCallback();

  const rows = context.document.getElementById('performance-results').children;
  assert.equal(rows.length, 2);
  assert.match(rows[0].textContent, /1\/6 · same_process · serving only · p95 30 ms/);
  assert.match(rows[0].textContent, /Itraining 1.4 · Ifull 1.7/);
  assert.match(rows[1].textContent, /4\/6 · same_host_split · serving only · p95 18 ms/);
});

test('graceful stop remains available while active and after a persisted dispatch request', async () => {
  for (const status of [
    'population_pending', 'population_ready', 'approved', 'running', 'stop_requested',
  ]) {
    const context = await initializeController({ status });
    assert.equal(context.document.getElementById('performance-stop').disabled, false,
      `stop should be enabled for ${status}`);
  }
});

test('independent progress polling failure cannot mutate or stop a trial', async () => {
  const context = await initializeController({ failPoll: true });
  const before = {
    approve: context.document.getElementById('performance-approve').disabled,
    stop: context.document.getElementById('performance-stop').disabled,
    create: context.document.getElementById('performance-create').disabled,
  };

  await context.intervalCallback();

  assert.match(context.document.getElementById('performance-rate').textContent,
    /progress temporarily unavailable; trial continues independently/i);
  assert.deepEqual({
    approve: context.document.getElementById('performance-approve').disabled,
    stop: context.document.getElementById('performance-stop').disabled,
    create: context.document.getElementById('performance-create').disabled,
  }, before);
  assert.equal(context.calls.some(([, options]) => options.method === 'POST'), false);
});

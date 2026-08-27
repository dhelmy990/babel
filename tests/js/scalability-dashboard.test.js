const test = require('node:test');
const assert = require('node:assert/strict');

const dashboard = require('../../backend/admin/scalability-dashboard.js');

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

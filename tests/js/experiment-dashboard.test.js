const test = require('node:test');
const assert = require('node:assert/strict');

const {
  activityView,
  healthView,
  isActiveExperiment,
  launchDefaults,
  launchView,
  modelOptions,
} = require('../../backend/admin/experiment-status.js');
const dashboard = require('../../backend/admin/experiment-dashboard.js');

const original = {
  schemaVersion: 1,
  modelId: '11111111-1111-4111-8111-111111111111',
  label: 'Original frozen encoder',
  parentModelId: null,
  immutable: true,
  compatible: true,
};

const child = {
  ...original,
  modelId: '22222222-2222-4222-8222-222222222222',
  label: 'June adapted child',
  parentModelId: original.modelId,
};

test('original and immutable child remain independently selectable', () => {
  assert.deepEqual(modelOptions([original, child]), [{
    value: original.modelId,
    label: 'Original frozen encoder · original',
    disabled: false,
    immutable: true,
    reason: '',
  }, {
    value: child.modelId,
    label: 'June adapted child · child',
    disabled: false,
    immutable: true,
    reason: '',
  }]);
});

test('incompatible models are visible with a disabled reason', () => {
  const options = modelOptions([{ ...child, compatible: false, incompatibilityReason: 'wrong embedding space' }]);
  assert.equal(options[0].disabled, true);
  assert.equal(options[0].reason, 'wrong embedding space');
});

test('launch defaults use the representative pgvector June to July path', () => {
  assert.deepEqual(launchDefaults(), {
    retrievalBackend: 'pgvector',
    creatorCount: 50,
    scenario: 'june_to_july',
    eventBudgetPerMonth: 100,
    runSeed: 0,
  });
  assert.equal(isActiveExperiment({ status: 'running' }), true);
  assert.equal(isActiveExperiment({ status: 'checkpointing' }), true);
  assert.equal(isActiveExperiment({ status: 'completed' }), false);
  assert.deepEqual(launchView({
    status: 'running', retrievalBackend: 'hnswlib', creatorCount: 75,
    environmentSequence: ['2026-06'],
  }), {
    retrievalBackend: 'hnswlib', creatorCount: 75, scenario: 'june_only', disabled: true,
  });
});

test('status health contains only public serving and training signals', () => {
  const view = healthView({
    status: 'running', createdBabelCount: 12, feedbackCount: 31, eventRate: 4.25,
    kafkaOffset: 310, kafkaLag: 2, trainerSteps: 19, rollingRankLoss: 0.14,
    checkpointPath: 'artifacts/run/checkpoint.pt', servingSynced: true,
    activeModelVersion: 3,
  });
  assert.deepEqual(view, [
    ['Created Babels', '12'], ['Feedback', '31'], ['Event rate', '4.25/s'],
    ['Kafka', 'offset 310 · lag 2'], ['Trainer', 'step 19 · rank loss 0.14'],
    ['Checkpoint', 'artifacts/run/checkpoint.pt'], ['Serving sync', 'synced · model v3'],
  ]);
  assert.doesNotMatch(JSON.stringify(view), /vectorLoss|relationalLoss|ppr|graph|clickstream|profile|random/i);
});

test('recommendation activity renders the observable decision path', () => {
  const row = activityView({
    occurredAtNs: 1720000000000000000,
    component: 'serving', event: 'recommendation.created', message: 'Candidate set scored',
    details: {
      kind: 'recommendation', creatorId: 'creator-1', newBabelId: 'babel-new',
      newBabelTitle: 'New Babel', candidateBabelIds: ['a', 'b', 'c'],
      includeBabelIds: ['a'], excludeBabelIds: ['b'], ignoreBabelIds: ['c'],
      acceptedEdgeCount: 1, modelId: 'model-1', modelVersion: 4,
    },
  });
  assert.match(row.summary, /creator-1/);
  assert.match(row.summary, /New Babel/);
  assert.match(row.summary, /candidates a, b, c/);
  assert.match(row.summary, /include a/);
  assert.match(row.summary, /exclude b/);
  assert.match(row.summary, /ignore c/);
  assert.match(row.summary, /1 accepted edge/);
  assert.match(row.summary, /model-1 v4/);
});

test('feedback, training, synchronization, and lifecycle activity are readable', () => {
  assert.match(activityView({ message: 'feedback', details: { kind: 'feedback', kafkaOffset: 8, kafkaLag: 1 } }).summary, /offset 8 · lag 1/);
  assert.match(activityView({ message: 'trained', details: { kind: 'training', trainerStep: 9, rollingRankLoss: 0.3 } }).summary, /step 9 · rolling rank loss 0.3/);
  assert.match(activityView({ message: 'synced', details: { kind: 'synchronization', checkpointPath: 'ckpt', synchronizationVersion: 2, modelId: 'm', modelVersion: 3 } }).summary, /ckpt · sync v2 · m v3/);
  assert.equal(activityView({ message: 'Run draining', details: { kind: 'lifecycle' } }).summary, 'Run draining');
});

test('scaled run health exposes placement cache origins and model staleness when persisted', () => {
  const view = healthView({
    createdBabelCount: 10, feedbackCount: 20, eventRate: 2, kafkaOffset: 20,
    kafkaLag: 1, trainerSteps: 4, checkpointPath: 'ckpt', servingSynced: false,
    activeModelVersion: 2, placement: { topology: 'same_host_split', servingPid: 11, trainerPid: 12 },
    sourceVectorOrigins: { qwen_encode: 2, cache_hit: 8, pgvector_load: 10 },
    trainerServingStalenessSteps: 3,
  });
  assert.deepEqual(view.slice(-3), [
    ['Placement', 'same_host_split · serving 11 · trainer 12'],
    ['Vector origins', 'qwen 2 · cache 8 · pgvector 10'],
    ['Model staleness', '3 trainer steps'],
  ]);
  const activity = activityView({
    component: 'serving', event: 'recommendation',
    details: {
      kind: 'recommendation', creatorId: 'c', newBabelTitle: 'Source', newBabelId: 'b',
      candidateBabelIds: [], includeBabelIds: [], excludeBabelIds: [], ignoreBabelIds: [],
      acceptedEdgeCount: 0, modelId: 'm', modelVersion: 2, requestId: 'req',
      traversalSessionId: 'walk', sourceVectorOrigin: 'pgvector_load',
    },
  });
  assert.match(activity.summary, /request req/);
  assert.match(activity.summary, /walk walk/);
  assert.match(activity.summary, /vector pgvector_load/);
});

test('start posts only the public launch request with the admin nonce', async () => {
  const body = { startingModelId: original.modelId, retrievalBackend: 'pgvector', creatorCount: 50, scenario: 'june_to_july', eventBudgetPerMonth: 100, runSeed: 0 };
  const result = await dashboard.startExperiment(async (url, options) => {
    assert.equal(url, '/admin/api/v1/experiment/runs');
    assert.equal(options.method, 'POST');
    assert.equal(options.headers['X-Babel-Admin-Nonce'], 'nonce');
    assert.deepEqual(JSON.parse(options.body), body);
    return { ok: true, status: 202, json: async () => ({ run: { runId: 'run-1', status: 'starting' } }) };
  }, 'nonce', body);
  assert.equal(result.run.runId, 'run-1');
});

test('graceful stop has no body and never exposes a kill action', async () => {
  const result = await dashboard.stopExperiment(async (url, options) => {
    assert.equal(url, '/admin/api/v1/experiment/runs/run-1/graceful-stop');
    assert.equal(options.method, 'POST');
    assert.equal(Object.hasOwn(options, 'body'), false);
    return { ok: true, status: 202, json: async () => ({ runId: 'run-1', status: 'stop_requested' }) };
  }, 'nonce', 'run-1');
  assert.equal(result.status, 'stop_requested');
  assert.equal(Object.hasOwn(dashboard, 'killExperiment'), false);
});

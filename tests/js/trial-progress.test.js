const test = require('node:test');
const assert = require('node:assert/strict');

const progress = require('../../backend/admin/trial-progress.js');

test('progress mapper is pure and reports rate ETA and matrix position', () => {
  const snapshot = Object.freeze({
    phase: 'measuring', conditionIndex: 3, conditionCount: 9,
    seededArticles: 5000, targetSeededArticles: 5000,
    createdBabels: 2500, targetCreatedBabels: 10000,
    indexedBabels: 2400, requested: 800, completed: 750,
    elapsedSeconds: 150, recentRate: 25, draining: false,
  });

  assert.deepEqual(progress.progressView(snapshot), {
    phase: 'measuring', workPhase: 'measuring', condition: '3/9', seeded: '5000/5000',
    created: '2500/10000', indexed: '2400/10000', requested: 800,
    submitted: 750, completed: 750, errors: 0, inFlight: 0,
    elapsed: '2m 30s', rate: '25.00/s', eta: '5m 0s',
    percent: 25, draining: false,
  });
  assert.equal(snapshot.createdBabels, 2500);
});

test('progress poller performs only persisted read requests', async () => {
  const calls = [];
  const views = [];
  const poller = progress.createTrialProgressPoller({
    fetchImpl: async (url, options) => {
      calls.push([url, options]);
      return { ok: true, json: async () => ({ trial: { progress: { phase: 'population', createdBabels: 1, targetCreatedBabels: 10 } } }) };
    },
    render: (view) => views.push(view),
  });

  await poller.poll('trial-1');
  assert.deepEqual(calls, [['/admin/api/v1/performance/trial-1', { method: 'GET', headers: { Accept: 'application/json' } }]]);
  assert.equal(views[0].phase, 'population');
  assert.equal(Object.hasOwn(poller, 'startTrainer'), false);
  assert.equal(Object.hasOwn(poller, 'mutate'), false);
});

test('condition progress exposes submitted completed error and in-flight counts', () => {
  const view = progress.progressView({
    phase: 'scheduled', conditionIndex: 6, conditionCount: 9,
    createdBabels: 10000, targetCreatedBabels: 10000,
    requested: 750, completed: 300, elapsedSeconds: 60, recentRate: 5,
    telemetry: {
      submitted: 320, errors: 2, inFlight: 20, conditionPhase: 'scheduled',
    },
  });

  assert.equal(view.condition, '6/9');
  assert.equal(view.workPhase, 'scheduled');
  assert.equal(view.submitted, 320);
  assert.equal(view.completed, 300);
  assert.equal(view.errors, 2);
  assert.equal(view.inFlight, 20);
  assert.equal(view.percent, 40);
  assert.equal(view.eta, '1m 30s');
});

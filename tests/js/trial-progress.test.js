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
    phase: 'measuring', condition: '3/9', seeded: '5000/5000',
    created: '2500/10000', indexed: '2400/10000', requested: 800,
    completed: 750, elapsed: '2m 30s', rate: '25.00/s', eta: '5m 0s',
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

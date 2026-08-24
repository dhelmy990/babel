const test = require('node:test');
const assert = require('node:assert/strict');

const {
  escapeHtml,
  seedErrorViewModel,
  seedViewModel,
} = require('../../backend/admin/seed-status.js');
const dashboard = require('../../backend/admin/dashboard.js');

test('unseeded state offers the initial action', () => {
  assert.deepEqual(seedViewModel({
    state: 'not_started', total: 80, completed: 0, skipped: 0, failed: 0,
  }), {
    label: 'Seed 80 Babels',
    disabled: false,
    percent: 0,
    summary: 'No Wikipedia Babels imported',
  });
});

test('partial completion offers retry and preserves errors', () => {
  const model = seedViewModel({
    state: 'completed_with_errors', total: 80, completed: 77, skipped: 0, failed: 3,
  });

  assert.equal(model.label, 'Retry 3 missing');
  assert.equal(model.disabled, false);
  assert.equal(model.percent, 96);
});

test('queued and running states are the only disabled actions', () => {
  const statuses = [
    ['queued', true],
    ['running', true],
    ['completed', false],
    ['failed', false],
    ['interrupted', false],
  ];

  for (const [state, disabled] of statuses) {
    const model = seedViewModel({ state, total: 3, completed: 1, skipped: 0, failed: 1 });
    assert.equal(model.disabled, disabled, state);
  }
});

test('percentage uses completed items, floors the fraction, and guards zero totals', () => {
  assert.equal(seedViewModel({ state: 'running', total: 3, completed: 2 }).percent, 66);
  assert.equal(seedViewModel({ state: 'completed', total: 0, completed: 0 }).percent, 0);
});

test('error view models keep plain text for textContent and provide escaped copies', () => {
  const errors = seedErrorViewModel([
    { article: 'A < B', message: 'Bad <script>alert("x")</script> & retry' },
  ]);

  assert.deepEqual(errors, [{
    article: 'A < B',
    message: 'Bad <script>alert("x")</script> & retry',
    escapedArticle: 'A &lt; B',
    escapedMessage: 'Bad &lt;script&gt;alert(&quot;x&quot;)&lt;/script&gt; &amp; retry',
  }]);
  assert.equal(escapeHtml("'\"&<>"), '&#39;&quot;&amp;&lt;&gt;');
});

test('request helpers use the fixed endpoint and attach to a conflicting active run', async () => {
  const status = await dashboard.requestSeedStatus(async (url) => {
    assert.equal(url, '/admin/api/v1/seed');
    return { ok: true, status: 200, json: async () => ({ state: 'queued' }) };
  });
  const result = await dashboard.startSeedRun(async (url, options) => {
    assert.equal(url, '/admin/api/v1/seed');
    assert.equal(options.headers['X-Babel-Admin-Nonce'], 'test-nonce');
    return { ok: false, status: 409, json: async () => ({ state: 'running' }) };
  }, 'test-nonce');

  assert.equal(status.state, 'queued');
  assert.deepEqual(result, { attached: true, status: { state: 'running' } });
});

const test = require('node:test');
const assert = require('node:assert/strict');

const {
  escapeHtml,
  seedErrorViewModel,
  seedViewModel,
} = require('../../backend/admin/seed-status.js');
const dashboard = require('../../backend/admin/dashboard.js');

function deferred() {
  let resolve;
  const promise = new Promise((completion) => { resolve = completion; });
  return { promise, resolve };
}

function fakeDocument() {
  const elements = new Map();
  const element = () => ({
    children: [],
    disabled: false,
    textContent: '',
    value: 0,
    attributes: new Map(),
    addEventListener() {},
    appendChild(child) { this.children.push(child); },
    replaceChildren() { this.children = []; },
    setAttribute(name, value) { this.attributes.set(name, value); },
  });

  for (const id of ['seed-action', 'seed-status', 'seed-counts', 'seed-progress', 'seed-current', 'seed-errors']) {
    elements.set(id, element());
  }

  return {
    elements,
    createElement: element,
    getElementById(id) { return elements.get(id); },
    querySelector() { return { content: 'test-nonce' }; },
  };
}

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

test('interrupted work resumes every assignment that remains unimported', () => {
  const model = seedViewModel({
    state: 'interrupted', total: 80, completed: 40, skipped: 10, failed: 2,
  });

  assert.equal(model.label, 'Resume 30 remaining');
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

test('skipped assignments satisfy progress and completed summaries', () => {
  const model = seedViewModel({ state: 'completed', total: 80, completed: 0, skipped: 80 });
  assert.equal(model.percent, 100);
  assert.equal(model.summary, '0 imported; 80 already present');
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

test('status requests reject malformed success payloads and nested error messages', async () => {
  await assert.rejects(
    dashboard.requestSeedStatus(async () => ({ ok: true, status: 200, json: async () => ({ runId: 'not-status' }) })),
    /invalid status payload/,
  );
  await assert.rejects(
    dashboard.requestSeedStatus(async () => ({ ok: false, status: 500, json: async () => ({ error: { message: 'database unavailable' } }) })),
    /database unavailable/,
  );
});

test('a timed-out GET recovers the action state', async () => {
  const documentRef = fakeDocument();
  let triggerTimeout;
  const controller = dashboard.createDashboardController({
    document: documentRef,
    fetchImpl: async (_url, options) => new Promise((_, reject) => options.signal.addEventListener('abort', () => reject(new Error('timed out')))),
    setTimeoutImpl: (callback) => { triggerTimeout = callback; return 1; },
    clearTimeoutImpl() {},
  });
  const refresh = controller.refresh();
  triggerTimeout();
  await refresh;
  assert.equal(documentRef.elements.get('seed-action').disabled, false);
  assert.match(documentRef.elements.get('seed-status').textContent, /Unable to reach seed service/);
});

test('an initial stale status cannot stop polling started by a successful seed request', async () => {
  const documentRef = fakeDocument();
  const initial = deferred();
  const recovery = deferred();
  const intervals = [];
  const cleared = [];
  let getCalls = 0;
  const controller = dashboard.createDashboardController({
    document: documentRef,
    fetchImpl: async (_url, options = {}) => {
      if (options.method === 'POST') {
        return { ok: true, status: 202, json: async () => ({ state: 'queued', total: 80 }) };
      }
      getCalls += 1;
      return getCalls === 1 ? initial.promise : recovery.promise;
    },
    setIntervalImpl: (callback, delay) => {
      intervals.push({ callback, delay });
      return intervals.length;
    },
    clearIntervalImpl: (id) => cleared.push(id),
  });

  const initialRefresh = controller.refresh();
  const start = controller.start();
  await new Promise((resolve) => setImmediate(resolve));
  assert.equal(intervals.length, 1);

  initial.resolve({ ok: true, status: 200, json: async () => ({ state: 'completed', total: 80, completed: 80 }) });
  await initialRefresh;
  assert.deepEqual(cleared, []);

  recovery.resolve({ ok: true, status: 200, json: async () => ({ state: 'queued', total: 80 }) });
  await start;
  assert.equal(documentRef.elements.get('seed-status').textContent, 'Preparing Wikipedia imports');
  assert.equal(intervals[0].delay, 1000);
});

test('a failed POST followed by a failed recovery GET re-enables the seed action', async () => {
  const documentRef = fakeDocument();
  const controller = dashboard.createDashboardController({
    document: documentRef,
    fetchImpl: async () => { throw new Error('offline'); },
    setIntervalImpl: () => 1,
    clearIntervalImpl() {},
  });

  await controller.start();

  assert.equal(documentRef.elements.get('seed-action').disabled, false);
  assert.match(documentRef.elements.get('seed-status').textContent, /Unable to reach seed service/);
});

test('a slow poll GET is not superseded by the next interval tick', async () => {
  const documentRef = fakeDocument();
  const slowStatus = deferred();
  const intervals = [];
  const cleared = [];
  let getCalls = 0;
  const controller = dashboard.createDashboardController({
    document: documentRef,
    fetchImpl: async () => {
      getCalls += 1;
      if (getCalls === 1) {
        return { ok: true, status: 200, json: async () => ({ state: 'running', total: 80, completed: 1 }) };
      }
      return slowStatus.promise;
    },
    setIntervalImpl: (callback, delay) => {
      intervals.push({ callback, delay });
      return intervals.length;
    },
    clearIntervalImpl: (id) => cleared.push(id),
  });

  await controller.refresh();
  const firstTick = intervals[0].callback();
  const secondTick = intervals[0].callback();
  assert.equal(getCalls, 2);

  slowStatus.resolve({ ok: true, status: 200, json: async () => ({ state: 'completed', total: 80, completed: 80 }) });
  await Promise.all([firstTick, secondTick]);
  assert.equal(documentRef.elements.get('seed-status').textContent, '80 imported; 0 already present');
  assert.deepEqual(cleared, [1]);
});

test('a runId-only POST keeps the action disabled through recovery', async () => {
  const documentRef = fakeDocument();
  const recovery = deferred();
  let posts = 0;
  const controller = dashboard.createDashboardController({
    document: documentRef,
    fetchImpl: async (_url, options = {}) => {
      if (options.method === 'POST') {
        posts += 1;
        return { ok: true, status: 202, json: async () => ({ runId: 'run-1' }) };
      }
      return recovery.promise;
    },
  });
  const start = controller.start();
  await new Promise((resolve) => setImmediate(resolve));
  assert.equal(documentRef.elements.get('seed-action').disabled, true);
  await controller.start();
  assert.equal(posts, 1);
  recovery.resolve({ ok: true, status: 200, json: async () => ({ state: 'completed', total: 1, completed: 1 }) });
  await start;
  assert.equal(documentRef.elements.get('seed-action').disabled, false);
});

test('stop aborts an active GET and prevents its result from rendering', async () => {
  const documentRef = fakeDocument();
  const response = deferred();
  let signal;
  const controller = dashboard.createDashboardController({
    document: documentRef,
    fetchImpl: async (_url, options) => { signal = options.signal; return response.promise; },
  });
  const refresh = controller.refresh();
  controller.stop();
  assert.equal(signal.aborted, true);
  response.resolve({ ok: true, status: 200, json: async () => ({ state: 'queued', total: 1 }) });
  await refresh;
  assert.equal(documentRef.elements.get('seed-status').textContent, '');
});

test('a hung POST times out and unlocks the action after recovery', async () => {
  const documentRef = fakeDocument();
  const timers = [];
  let postSignal;
  const controller = dashboard.createDashboardController({
    document: documentRef,
    fetchImpl: async (_url, options = {}) => {
      if (options.method === 'POST') {
        postSignal = options.signal;
        return new Promise((_, reject) => options.signal.addEventListener('abort', () => reject(new Error('timed out'))));
      }
      return { ok: true, status: 200, json: async () => ({ state: 'completed', total: 1, completed: 1 }) };
    },
    setTimeoutImpl: (callback) => { timers.push(callback); return timers.length; },
    clearTimeoutImpl() {},
  });
  const start = controller.start();
  await new Promise((resolve) => setImmediate(resolve));
  timers[0]();
  await start;
  assert.equal(postSignal.aborted, true);
  assert.equal(documentRef.elements.get('seed-action').disabled, false);
});

test('a late POST response after stop cannot render or start polling', async () => {
  const documentRef = fakeDocument();
  const post = deferred();
  const intervals = [];
  const controller = dashboard.createDashboardController({
    document: documentRef,
    fetchImpl: async () => post.promise,
    setIntervalImpl: (callback) => { intervals.push(callback); return intervals.length; },
  });
  const start = controller.start();
  controller.stop();
  post.resolve({ ok: true, status: 202, json: async () => ({ state: 'queued', total: 1 }) });
  await start;
  assert.equal(documentRef.elements.get('seed-status').textContent, '');
  assert.equal(intervals.length, 0);
});

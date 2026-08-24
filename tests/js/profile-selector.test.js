const test = require('node:test');
const assert = require('node:assert/strict');

const {
  applyProfileGraph,
  canMutate,
  createBackendRequest,
  orderedProfiles,
  profileGraphPath,
  toRendererGraph,
} = require('../../js/profile-selector.js');

const personal = {
  id: 'personal',
  displayName: 'Personal',
  color: '#F4E7D3',
  order: 0,
};

const generated = {
  id: 'generated',
  displayName: 'Generated',
  color: '#3DDC97',
  order: 1,
};

test('profiles are ordered by backend order with Personal first', () => {
  const input = [generated, personal];
  const profiles = orderedProfiles(input);

  assert.deepEqual(profiles, [personal, generated]);
  assert.notEqual(profiles, input);
  assert.deepEqual(input, [generated, personal]);
});

test('profiles reject malformed DTOs rather than exposing partial selector rows', () => {
  assert.throws(() => orderedProfiles('not-an-array'), /profiles/i);
  assert.throws(
    () => orderedProfiles([{ ...personal, color: 'red' }]),
    /color/i,
  );
  assert.throws(
    () => orderedProfiles([{ ...personal, order: 1.5 }]),
    /order/i,
  );
});

test('backend HTML and edge IDs map to the renderer representation', () => {
  const graph = toRendererGraph({
    profile: generated,
    babels: [{
      id: 'babel-a',
      title: 'Film',
      contentHtml: '<p>Film</p>',
      color: '#fff',
      contentRevision: 2,
    }, {
      id: 'babel-b',
      title: 'Music',
      contentHtml: '<p>Music</p>',
      color: '#000000',
      contentRevision: 1,
    }],
    edges: [{ id: 'edge-a-b', sourceId: 'babel-a', targetId: 'babel-b' }],
  });

  assert.deepEqual(graph, {
    babels: [{
      id: 'babel-a', title: 'Film', description: '<p>Film</p>', color: '#fff',
    }, {
      id: 'babel-b', title: 'Music', description: '<p>Music</p>', color: '#000000',
    }],
    edges: [{ id: 'edge-a-b', source: 'babel-a', target: 'babel-b' }],
  });
});

test('graph mapping rejects dangling edges and malformed arrays', () => {
  assert.throws(
    () => toRendererGraph({ profile: personal, babels: {}, edges: [] }),
    /babels/i,
  );
  assert.throws(
    () => toRendererGraph({
      profile: personal,
      babels: [{
        id: 'a', title: 'A', contentHtml: '', color: '#fff', contentRevision: 1,
      }],
      edges: [{ id: 'edge', sourceId: 'a', targetId: 'missing' }],
    }),
    /endpoint/i,
  );
});

test('graph mapping validates backend content revisions', () => {
  assert.throws(
    () => toRendererGraph({
      profile: personal,
      babels: [{
        id: 'a', title: 'A', contentHtml: '', color: '#fff', contentRevision: 0,
      }],
      edges: [],
    }),
    /contentRevision/i,
  );
});

test('applying an empty graph replaces prior arrays and enters read-only mode', () => {
  const state = {
    babels: [{ id: 'stale' }],
    edges: [{ id: 'stale-edge' }],
    selectedBabel: { id: 'stale' },
    comparisonBabels: [{ id: 'stale' }],
    editingBabel: { id: 'stale' },
    isCreating: true,
    selectedSimilarBabels: ['stale'],
  };

  applyProfileGraph(state, { profile: personal, babels: [], edges: [] });

  assert.deepEqual(state.babels, []);
  assert.deepEqual(state.edges, []);
  assert.equal(state.currentProfile, personal);
  assert.equal(state.isReadOnlyProfile, true);
  assert.equal(state.selectedBabel, null);
  assert.deepEqual(state.comparisonBabels, []);
  assert.equal(state.editingBabel, null);
  assert.equal(state.isCreating, false);
  assert.deepEqual(state.selectedSimilarBabels, []);
  assert.equal(canMutate(state), false);
});

test('profile graph paths encode IDs and reject empty IDs', () => {
  assert.equal(
    profileGraphPath('creator/with spaces'),
    '/api/v1/profiles/creator%2Fwith%20spaces/graph',
  );
  assert.throws(() => profileGraphPath(''), /profile id/i);
});

test('backend requests are bounded local JSON GETs', async () => {
  let captured;
  const request = createBackendRequest({
    baseUrl: 'http://127.0.0.1:9999/',
    timeoutMs: 75,
    fetchImpl: async (url, options) => {
      captured = { url, options };
      return {
        ok: true,
        status: 200,
        headers: { get: () => 'application/json; charset=utf-8' },
        text: async () => '{"profiles":[]}',
      };
    },
  });

  assert.deepEqual(await request('/api/v1/profiles'), { profiles: [] });
  assert.equal(captured.url, 'http://127.0.0.1:9999/api/v1/profiles');
  assert.equal(captured.options.method, 'GET');
  assert.equal(captured.options.headers.Accept, 'application/json');
  assert.ok(captured.options.signal);
});

test('backend helper rejects non-local URLs, bad status, and non-JSON bodies safely', async () => {
  assert.throws(
    () => createBackendRequest({ baseUrl: 'https://example.com' }),
    /local backend url/i,
  );

  const failing = createBackendRequest({
    fetchImpl: async () => ({
      ok: false,
      status: 503,
      headers: { get: () => 'application/json' },
      text: async () => '{"error":{"message":"database down; password=secret"}}',
    }),
  });
  await assert.rejects(failing('/api/v1/profiles'), /Backend request failed \(503\)/);
  await assert.rejects(failing('/api/v1/profiles'), { message: 'Backend request failed (503)' });

  const malformed = createBackendRequest({
    fetchImpl: async () => ({
      ok: true,
      status: 200,
      headers: { get: () => 'text/html' },
      text: async () => '<html>not JSON</html>',
    }),
  });
  await assert.rejects(malformed('/api/v1/profiles'), /invalid JSON response/i);
});

test('backend helper aborts requests that exceed its timeout', async () => {
  let scheduled;
  const request = createBackendRequest({
    timeoutMs: 10,
    setTimeoutImpl: (callback) => { scheduled = callback; return 4; },
    clearTimeoutImpl() {},
    fetchImpl: async (_url, { signal }) => new Promise((resolve, reject) => {
      signal.addEventListener('abort', () => reject(new Error('raw transport detail')));
    }),
  });

  const result = request('/api/v1/profiles');
  scheduled();
  await assert.rejects(result, { message: 'Backend request timed out' });
});

test('backend timeout remains active while the JSON body is being read', async () => {
  let scheduled;
  let requestSignal;
  const request = createBackendRequest({
    timeoutMs: 10,
    setTimeoutImpl: (callback) => { scheduled = callback; return 5; },
    clearTimeoutImpl() {},
    fetchImpl: async (_url, { signal }) => {
      requestSignal = signal;
      return {
        ok: true,
        status: 200,
        headers: { get: () => 'application/json' },
        text: async () => new Promise((resolve, reject) => {
          requestSignal.addEventListener('abort', () => reject(new Error('raw body error')));
        }),
      };
    },
  });

  const result = request('/api/v1/profiles');
  await new Promise((resolve) => setImmediate(resolve));
  scheduled();
  await assert.rejects(result, { message: 'Backend request timed out' });
});

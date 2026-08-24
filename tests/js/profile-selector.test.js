const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const {
  applyProfileGraph,
  canMutate,
  createBackendRequest,
  escapeHtml,
  htmlToPlainText,
  orderedProfiles,
  profileGraphPath,
  toRendererGraph,
  wheelDirection,
} = require('../../js/profile-selector.js');

const personal = {
  id: 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa',
  displayName: 'Personal',
  color: '#F4E7D3',
  order: 0,
};

const generated = {
  id: 'bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb',
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
  for (const id of ['__proto__', 'hasOwnProperty', `${'a'.repeat(36)}x`]) {
    assert.throws(
      () => orderedProfiles([{ ...personal, id }]),
      /UUID/i,
    );
  }
});

test('backend HTML and edge IDs map to the renderer representation', () => {
  const graph = toRendererGraph({
    profile: generated,
    babels: [{
      id: 'cccccccc-cccc-4ccc-8ccc-cccccccccccc',
      title: 'Film',
      contentHtml: '<p>Film</p>',
      color: '#fff',
      contentRevision: 2,
    }, {
      id: 'dddddddd-dddd-4ddd-8ddd-dddddddddddd',
      title: 'Music',
      contentHtml: '<p>Music</p>',
      color: '#000000',
      contentRevision: 1,
    }],
    edges: [{
      id: 'eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee',
      sourceId: 'cccccccc-cccc-4ccc-8ccc-cccccccccccc',
      targetId: 'dddddddd-dddd-4ddd-8ddd-dddddddddddd',
    }],
  });

  assert.deepEqual(graph, {
    babels: [{
      id: 'cccccccc-cccc-4ccc-8ccc-cccccccccccc',
      title: 'Film', description: '<p>Film</p>', color: '#fff',
    }, {
      id: 'dddddddd-dddd-4ddd-8ddd-dddddddddddd',
      title: 'Music', description: '<p>Music</p>', color: '#000000',
    }],
    edges: [{
      id: 'eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee',
      source: 'cccccccc-cccc-4ccc-8ccc-cccccccccccc',
      target: 'dddddddd-dddd-4ddd-8ddd-dddddddddddd',
    }],
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
        id: 'cccccccc-cccc-4ccc-8ccc-cccccccccccc',
        title: 'A', contentHtml: '', color: '#fff', contentRevision: 1,
      }],
      edges: [{
        id: 'eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee',
        sourceId: 'cccccccc-cccc-4ccc-8ccc-cccccccccccc',
        targetId: 'ffffffff-ffff-4fff-8fff-ffffffffffff',
      }],
    }),
    /endpoint/i,
  );
});

test('graph mapping validates backend content revisions', () => {
  assert.throws(
    () => toRendererGraph({
      profile: personal,
      babels: [{
        id: 'cccccccc-cccc-4ccc-8ccc-cccccccccccc',
        title: 'A', contentHtml: '', color: '#fff', contentRevision: 0,
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

test('profile graph paths accept only canonical UUIDs', () => {
  assert.equal(
    profileGraphPath(personal.id),
    `/api/v1/profiles/${personal.id}/graph`,
  );
  assert.throws(() => profileGraphPath(''), /profile id/i);
  assert.throws(() => profileGraphPath('__proto__'), /UUID/i);
});

test('graph mapping rejects a three-node cycle before DAG layout', () => {
  const ids = [
    '11111111-1111-4111-8111-111111111111',
    '22222222-2222-4222-8222-222222222222',
    '33333333-3333-4333-8333-333333333333',
  ];
  const babels = ids.map((id, index) => ({
    id,
    title: `Node ${index}`,
    contentHtml: '<p>Safe</p>',
    color: '#fff',
    contentRevision: 1,
  }));
  const edges = ids.map((sourceId, index) => ({
    id: [
      '44444444-4444-4444-8444-444444444444',
      '55555555-5555-4555-8555-555555555555',
      '66666666-6666-4666-8666-666666666666',
    ][index],
    sourceId,
    targetId: ids[(index + 1) % ids.length],
  }));

  assert.throws(
    () => toRendererGraph({ profile: generated, babels, edges }),
    /cycle/i,
  );
});

test('tooltip titles are HTML escaped and comparison content is plain text', () => {
  assert.equal(
    escapeHtml('<img src=x onerror="alert(1)"> & Film'),
    '&lt;img src=x onerror=&quot;alert(1)&quot;&gt; &amp; Film',
  );
  assert.equal(
    htmlToPlainText('<p>Film <strong>history</strong><br>archive &amp; notes</p><ul><li>One</li><li>Two</li></ul>'),
    'Film history\narchive & notes\nOne\nTwo',
  );
});

test('wheel direction ignores zero input and supports horizontal wheels', () => {
  assert.equal(wheelDirection(0, 0), 0);
  assert.equal(wheelDirection(18, 0), 1);
  assert.equal(wheelDirection(-18, 0), -1);
  assert.equal(wheelDirection(80, -120), -1);
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
        url,
        headers: {
          get: (name) => name.toLowerCase() === 'content-type'
            ? 'application/json; charset=utf-8'
            : null,
        },
        body: new Response('{"profiles":[]}').body,
      };
    },
  });

  assert.deepEqual(await request('/api/v1/profiles'), { profiles: [] });
  assert.equal(captured.url, 'http://127.0.0.1:9999/api/v1/profiles');
  assert.equal(captured.options.method, 'GET');
  assert.equal(captured.options.headers.Accept, 'application/json');
  assert.equal(captured.options.redirect, 'error');
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
      url: 'http://127.0.0.1:8787/api/v1/profiles',
      headers: { get: () => 'application/json' },
      body: new Response('{"error":{"message":"database down"}}').body,
    }),
  });
  await assert.rejects(failing('/api/v1/profiles'), /Backend request failed \(503\)/);
  await assert.rejects(failing('/api/v1/profiles'), { message: 'Backend request failed (503)' });

  const malformed = createBackendRequest({
    fetchImpl: async () => ({
      ok: true,
      status: 200,
      url: 'http://127.0.0.1:8787/api/v1/profiles',
      headers: { get: () => 'text/html' },
      body: new Response('<html>not JSON</html>').body,
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
        url: 'http://127.0.0.1:8787/api/v1/profiles',
        headers: { get: (name) => name.toLowerCase() === 'content-type' ? 'application/json' : null },
        body: {
          getReader: () => ({
            read: () => new Promise((resolve, reject) => {
              requestSignal.addEventListener('abort', () => reject(new Error('raw body error')));
            }),
            cancel() {},
          }),
        },
      };
    },
  });

  const result = request('/api/v1/profiles');
  await new Promise((resolve) => setImmediate(resolve));
  scheduled();
  await assert.rejects(result, { message: 'Backend request timed out' });
});

test('backend helper rejects cross-origin final responses', async () => {
  const request = createBackendRequest({
    fetchImpl: async () => ({
      ok: true,
      status: 200,
      url: 'http://attacker.test/api/v1/profiles',
      headers: { get: () => 'application/json' },
      body: new Response('{"profiles":[]}').body,
    }),
  });
  await assert.rejects(request('/api/v1/profiles'), /origin/i);
});

test('backend helper rejects oversized Content-Length before reading', async () => {
  let readerOpened = false;
  const request = createBackendRequest({
    fetchImpl: async (_url) => ({
      ok: true,
      status: 200,
      url: 'http://127.0.0.1:8787/api/v1/profiles',
      headers: { get: (name) => name.toLowerCase() === 'content-length' ? '2097153' : 'application/json' },
      body: { getReader() { readerOpened = true; } },
    }),
  });
  await assert.rejects(request('/api/v1/profiles'), /too large/i);
  assert.equal(readerOpened, false);
});

test('backend helper cancels a streaming body once it exceeds two MiB', async () => {
  let canceled = false;
  let reads = 0;
  const request = createBackendRequest({
    fetchImpl: async () => ({
      ok: true,
      status: 200,
      url: 'http://127.0.0.1:8787/api/v1/profiles',
      headers: { get: (name) => name.toLowerCase() === 'content-type' ? 'application/json' : null },
      body: {
        getReader: () => ({
          async read() {
            reads += 1;
            return reads === 1
              ? { done: false, value: new Uint8Array(2 * 1024 * 1024 + 1) }
              : { done: true };
          },
          async cancel() { canceled = true; },
        }),
      },
    }),
  });
  await assert.rejects(request('/api/v1/profiles'), /too large/i);
  assert.equal(canceled, true);
});

test('backend helper clears its timeout when fetch fails', async () => {
  let cleared;
  const request = createBackendRequest({
    setTimeoutImpl: () => 77,
    clearTimeoutImpl: (handle) => { cleared = handle; },
    fetchImpl: async () => { throw new Error('transport detail'); },
  });
  await assert.rejects(request('/api/v1/profiles'), /Unable to connect/i);
  assert.equal(cleared, 77);
});

test('preload exposes only read-only profile IPC methods', async () => {
  const preloadPath = path.join(__dirname, '../../preload.js');
  const source = fs.readFileSync(preloadPath, 'utf8');
  const invocations = [];
  let exposed;

  vm.runInNewContext(source, {
    require(moduleName) {
      assert.equal(moduleName, 'electron');
      return {
        contextBridge: {
          exposeInMainWorld(name, api) {
            assert.equal(name, 'electronAPI');
            exposed = api;
          },
        },
        ipcRenderer: {
          invoke(channel, ...args) {
            invocations.push([channel, ...args]);
            return Promise.resolve({ success: true });
          },
        },
      };
    },
  });

  assert.deepEqual(Object.keys(exposed).sort(), [
    'listProfiles',
    'loadProfileGraph',
  ]);
  await exposed.listProfiles();
  await exposed.loadProfileGraph('profile-id');
  assert.deepEqual(invocations, [
    ['profiles:list'],
    ['profiles:graph', 'profile-id'],
  ]);
  assert.equal(exposed.save, undefined);
  assert.equal(exposed.saveFile, undefined);
});

test('renderer does not call legacy Electron APIs removed from preload', () => {
  const rendererFiles = [
    '../../js/app.js',
    '../../js/editor.js',
    '../../js/persistence.js',
    '../../js/ui.js',
  ];
  const forbiddenMethods = [
    'save',
    'load',
    'exportJSON',
    'importJSON',
    'openFile',
    'checkFileExists',
    'locateFile',
    'saveFile',
  ];

  for (const relativePath of rendererFiles) {
    const source = fs.readFileSync(path.join(__dirname, relativePath), 'utf8');
    for (const method of forbiddenMethods) {
      assert.doesNotMatch(
        source,
        new RegExp(`electronAPI\\?*\\.${method}\\b`),
        `${relativePath} must not call electronAPI.${method}`,
      );
    }
  }
});

(function attachDashboard(root, factory) {
  const api = factory(root, root.BabelSeedStatus);

  if (typeof module !== 'undefined' && module.exports) {
    module.exports = api;
  }

  root.BabelSeedDashboard = api;
}(typeof globalThis !== 'undefined' ? globalThis : this, function createDashboard(root, seedStatus) {
  const ENDPOINT = '/admin/api/v1/seed';
  const ACTIVE_STATES = new Set(['queued', 'running']);

  function isActiveSeedState(status) {
    return Boolean(status) && ACTIVE_STATES.has(status.state);
  }

  function errorMessage(payload, fallback) {
    if (!payload || typeof payload !== 'object') return fallback;
    return payload.message || payload.error || fallback;
  }

  async function responsePayload(response) {
    try {
      return await response.json();
    } catch (error) {
      throw new Error('Seed service returned invalid JSON');
    }
  }

  async function requestSeedStatus(fetchImpl) {
    let response;
    try {
      response = await fetchImpl(ENDPOINT, { headers: { Accept: 'application/json' } });
    } catch (error) {
      throw new Error(`Unable to reach seed service: ${error.message || 'network error'}`);
    }

    const payload = await responsePayload(response);
    if (!response.ok) {
      throw new Error(`Seed status request failed (${response.status}): ${errorMessage(payload, 'unknown error')}`);
    }
    return payload.status || payload;
  }

  async function startSeedRun(fetchImpl, nonce) {
    let response;
    try {
      response = await fetchImpl(ENDPOINT, {
        method: 'POST',
        headers: {
          Accept: 'application/json',
          'Content-Type': 'application/json',
          'X-Babel-Admin-Nonce': nonce || '',
        },
        body: '{}',
      });
    } catch (error) {
      throw new Error(`Unable to start seed run: ${error.message || 'network error'}`);
    }

    const payload = await responsePayload(response);
    if (response.status === 409) {
      return { attached: true, status: payload.status || payload };
    }
    if (!response.ok) {
      throw new Error(`Seed request failed (${response.status}): ${errorMessage(payload, 'unknown error')}`);
    }
    return { attached: false, status: payload.status || payload };
  }

  function createDashboardController(options) {
    const documentRef = options.document;
    const fetchImpl = options.fetchImpl || root.fetch.bind(root);
    const setIntervalImpl = options.setIntervalImpl || root.setInterval.bind(root);
    const clearIntervalImpl = options.clearIntervalImpl || root.clearInterval.bind(root);
    const elements = {
      action: documentRef.getElementById('seed-action'),
      status: documentRef.getElementById('seed-status'),
      counts: documentRef.getElementById('seed-counts'),
      progress: documentRef.getElementById('seed-progress'),
      current: documentRef.getElementById('seed-current'),
      errors: documentRef.getElementById('seed-errors'),
    };
    const nonce = documentRef.querySelector('meta[name="babel-admin-nonce"]')?.content || '';
    let pollTimer = null;
    let refreshPromise = null;
    let starting = false;

    function renderErrors(errors) {
      elements.errors.replaceChildren();
      for (const error of seedStatus.seedErrorViewModel(errors)) {
        const item = documentRef.createElement('li');
        item.textContent = `${error.article}: ${error.message}`;
        elements.errors.appendChild(item);
      }
    }

    function render(status) {
      const model = seedStatus.seedViewModel(status);
      const completed = Number(status.completed) || 0;
      const total = Number(status.total) || 0;
      const skipped = Number(status.skipped) || 0;
      const failed = Number(status.failed) || 0;
      const profile = status.current_profile || status.currentProfile || 'No profile active';
      const article = status.current_article || status.currentArticle || 'No article active';

      elements.action.textContent = model.label;
      elements.action.disabled = model.disabled || starting;
      elements.status.textContent = model.summary;
      elements.counts.textContent = `${completed}/${total} imported | ${skipped} skipped | ${failed} failed`;
      elements.progress.value = model.percent;
      elements.progress.setAttribute('aria-valuetext', `${model.percent}% complete`);
      elements.current.textContent = `${profile} / ${article}`;
      renderErrors(status.errors || status.live_errors || []);
      synchronizePolling(status);
    }

    function renderError(message) {
      elements.status.textContent = message;
      elements.errors.replaceChildren();
      const item = documentRef.createElement('li');
      item.textContent = message;
      elements.errors.appendChild(item);
    }

    function stopPolling() {
      if (pollTimer !== null) {
        clearIntervalImpl(pollTimer);
        pollTimer = null;
      }
    }

    function synchronizePolling(status) {
      if (!isActiveSeedState(status)) {
        stopPolling();
      } else if (pollTimer === null) {
        pollTimer = setIntervalImpl(refresh, 1000);
      }
    }

    async function refresh() {
      if (refreshPromise) return refreshPromise;
      refreshPromise = requestSeedStatus(fetchImpl)
        .then(render)
        .catch((error) => renderError(error.message))
        .finally(() => { refreshPromise = null; });
      return refreshPromise;
    }

    async function start() {
      if (starting) return;
      starting = true;
      elements.action.disabled = true;
      try {
        const result = await startSeedRun(fetchImpl, nonce);
        render(result.status);
      } catch (error) {
        renderError(error.message);
      } finally {
        starting = false;
        await refresh();
      }
    }

    elements.action.addEventListener('click', start);
    return { refresh, start, stop: stopPolling };
  }

  function initializeDashboard() {
    const controller = createDashboardController({ document: root.document });
    controller.refresh();
    root.addEventListener('beforeunload', controller.stop, { once: true });
    return controller;
  }

  if (root.document && root.document.readyState !== 'loading') {
    initializeDashboard();
  } else if (root.document) {
    root.document.addEventListener('DOMContentLoaded', initializeDashboard, { once: true });
  }

  return {
    ENDPOINT,
    createDashboardController,
    isActiveSeedState,
    requestSeedStatus,
    startSeedRun,
  };
}));

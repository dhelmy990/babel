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
    const error = payload.error;
    return payload.message || (error && typeof error === 'object' ? error.message : error) || fallback;
  }

  async function responsePayload(response) {
    try {
      return await response.json();
    } catch (error) {
      throw new Error('Seed service returned invalid JSON');
    }
  }

  async function requestSeedStatus(fetchImpl, signal) {
    let response;
    try {
      response = await fetchImpl(ENDPOINT, { headers: { Accept: 'application/json' }, signal });
    } catch (error) {
      throw new Error(`Unable to reach seed service: ${error.message || 'network error'}`);
    }

    const payload = await responsePayload(response);
    if (!response.ok) {
      throw new Error(`Seed status request failed (${response.status}): ${errorMessage(payload, 'unknown error')}`);
    }
    const status = seedStatus.normalizeSeedStatus(payload.status || payload);
    if (!status) throw new Error('Seed service returned invalid status payload');
    return status;
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
    const setTimeoutImpl = options.setTimeoutImpl || root.setTimeout.bind(root);
    const clearTimeoutImpl = options.clearTimeoutImpl || root.clearTimeout.bind(root);
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
    let starting = false;
    let lastStatus = null;
    let lifecycleGeneration = 0;
    let refreshPromise = null;
    let refreshGeneration = null;
    let activeAbort = null;
    let disposed = false;

    function renderErrors(errors) {
      elements.errors.replaceChildren();
      for (const error of seedStatus.seedErrorViewModel(errors)) {
        const item = documentRef.createElement('li');
        item.textContent = `${error.article}: ${error.message}`;
        elements.errors.appendChild(item);
      }
    }

    function renderAction(status) {
      const model = seedStatus.seedViewModel(status);

      elements.action.textContent = model.label;
      elements.action.disabled = model.disabled || starting;
      return model;
    }

    function render(status) {
      const model = renderAction(status);
      const { completed, total, skipped, failed } = seedStatus.normalizeSeedStatus(status);
      const profile = status.current_profile || status.currentProfile || 'No profile active';
      const article = status.current_article || status.currentArticle || 'No article active';

      elements.status.textContent = model.summary;
      elements.counts.textContent = `${completed}/${total} imported | ${skipped} skipped | ${failed} failed`;
      elements.progress.value = model.percent;
      elements.progress.setAttribute('aria-valuetext', `${model.percent}% complete`);
      elements.current.textContent = `${profile} / ${article}`;
      renderErrors(status.errors || status.live_errors || []);
      synchronizePolling(status);
    }

    function renderError(message) {
      renderAction(lastStatus || { state: 'not_started', total: 0, completed: 0, skipped: 0, failed: 0 });
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
      if (disposed || !isActiveSeedState(status)) {
        stopPolling();
      } else if (pollTimer === null) {
        pollTimer = setIntervalImpl(refresh, 1000);
      }
    }

    async function refresh() {
      if (disposed) return undefined;
      const responseGeneration = lifecycleGeneration;
      if (refreshPromise && refreshGeneration === responseGeneration) {
        return refreshPromise;
      }

      const abortController = typeof root.AbortController === 'function' ? new root.AbortController() : null;
      activeAbort = abortController;
      const timeout = setTimeoutImpl(() => abortController?.abort(), 10000);
      const pending = requestSeedStatus(fetchImpl, abortController?.signal)
        .then((status) => {
          if (!disposed && responseGeneration === lifecycleGeneration) {
            lastStatus = status;
            render(status);
          }
          return status;
        })
        .catch((error) => {
          if (!disposed && responseGeneration === lifecycleGeneration) {
            renderError(error.message);
          }
          return undefined;
        })
        .finally(() => {
          clearTimeoutImpl(timeout);
          if (activeAbort === abortController) activeAbort = null;
          if (refreshPromise === pending) {
            refreshPromise = null;
            refreshGeneration = null;
          }
        });
      refreshPromise = pending;
      refreshGeneration = responseGeneration;
      return pending;
    }

    async function start() {
      if (disposed || starting) return;
      starting = true;
      lifecycleGeneration += 1;
      renderAction(lastStatus || { state: 'not_started', total: 0, completed: 0, skipped: 0, failed: 0 });
      try {
        const result = await startSeedRun(fetchImpl, nonce);
        if (result.status && result.status.state) {
          lastStatus = result.status;
          render(result.status);
        }
      } catch (error) {
        renderError(error.message);
      } finally {
        await refresh();
        if (!disposed) {
          starting = false;
          renderAction(lastStatus || { state: 'not_started', total: 0, completed: 0, skipped: 0, failed: 0 });
        }
      }
    }

    elements.action.addEventListener('click', start);
    function stop() {
      disposed = true;
      lifecycleGeneration += 1;
      stopPolling();
      activeAbort?.abort();
    }
    return { refresh, start, stop };
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

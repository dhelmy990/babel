(function attachExperimentDashboard(root, factory) {
  const api = factory(root, root.BabelExperimentStatus);
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  root.BabelExperimentDashboard = api;
}(typeof globalThis !== 'undefined' ? globalThis : this, function createExperimentDashboard(root, view) {
  const BASE = '/admin/api/v1/experiment';

  function errorMessage(payload, fallback) {
    return payload?.error?.message || payload?.message || fallback;
  }

  async function jsonRequest(fetchImpl, url, options = {}) {
    let response;
    try {
      response = await fetchImpl(url, {
        ...options,
        headers: { Accept: 'application/json', ...(options.headers || {}) },
      });
    } catch (error) {
      throw new Error(`Unable to reach experiment service: ${error.message || 'network error'}`);
    }
    let payload;
    try {
      payload = await response.json();
    } catch (error) {
      throw new Error('Experiment service returned invalid JSON');
    }
    if (!response.ok) {
      throw new Error(`Experiment request failed (${response.status}): ${errorMessage(payload, 'unknown error')}`);
    }
    return payload;
  }

  function startExperiment(fetchImpl, nonce, body) {
    return jsonRequest(fetchImpl, `${BASE}/runs`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'X-Babel-Admin-Nonce': nonce },
      body: JSON.stringify(body),
    });
  }

  function stopExperiment(fetchImpl, nonce, runId) {
    return jsonRequest(fetchImpl,
      `${BASE}/runs/${encodeURIComponent(runId)}/graceful-stop`, {
        method: 'POST', headers: { 'X-Babel-Admin-Nonce': nonce },
      });
  }

  function createExperimentController(options) {
    const documentRef = options.document;
    const fetchImpl = options.fetchImpl || root.fetch.bind(root);
    const setIntervalImpl = options.setIntervalImpl || root.setInterval.bind(root);
    const clearIntervalImpl = options.clearIntervalImpl || root.clearInterval.bind(root);
    const ids = [
      'experiment-model', 'experiment-retrieval', 'experiment-scenario',
      'experiment-creators', 'experiment-budget', 'experiment-seed',
      'experiment-start', 'experiment-stop', 'experiment-status',
      'experiment-health', 'experiment-activity',
    ];
    const elements = Object.fromEntries(ids.map((id) => [id, documentRef.getElementById(id)]));
    const nonce = documentRef.querySelector('meta[name="babel-admin-nonce"]')?.content || '';
    let currentRun = null;
    let after = 0;
    let pollTimer = null;
    let requestPending = false;
    let refreshing = false;
    let disposed = false;

    function setStatus(message, error = false) {
      elements['experiment-status'].textContent = message;
      elements['experiment-status'].classList?.toggle('is-error', error);
    }

    function setControls(run) {
      const active = view.isActiveExperiment(run);
      for (const id of [
        'experiment-model', 'experiment-retrieval', 'experiment-scenario',
        'experiment-creators', 'experiment-budget', 'experiment-seed',
      ]) elements[id].disabled = active || requestPending;
      elements['experiment-start'].disabled = active || requestPending || !elements['experiment-model'].value;
      elements['experiment-stop'].disabled = !active || requestPending
        || !['starting', 'running'].includes(run?.status);
      if (active && pollTimer === null) pollTimer = setIntervalImpl(refresh, 1000);
      if (!active && pollTimer !== null) {
        clearIntervalImpl(pollTimer);
        pollTimer = null;
      }
    }

    function renderModels(models) {
      elements['experiment-model'].replaceChildren();
      for (const item of view.modelOptions(models)) {
        const option = documentRef.createElement('option');
        option.value = item.value;
        option.textContent = item.reason ? `${item.label} — ${item.reason}` : item.label;
        option.disabled = item.disabled;
        option.dataset.immutable = String(item.immutable);
        elements['experiment-model'].appendChild(option);
      }
    }

    function renderRun(run) {
      currentRun = run;
      const launch = view.launchView(run);
      elements['experiment-retrieval'].value = launch.retrievalBackend;
      elements['experiment-scenario'].value = launch.scenario;
      elements['experiment-creators'].value = launch.creatorCount;
      if (!run) {
        setStatus('No online experiment has run');
      } else {
        const model = run.activeModelId ? ` · model ${run.activeModelId} v${run.activeModelVersion}` : '';
        setStatus(`${run.status.replaceAll('_', ' ')} · ${run.createdBabelCount || 0} Babels${model}`,
          run.status === 'failed');
      }
      elements['experiment-health'].replaceChildren();
      for (const [label, value] of view.healthView(run)) {
        const item = documentRef.createElement('div');
        const term = documentRef.createElement('dt');
        const description = documentRef.createElement('dd');
        term.textContent = label;
        description.textContent = value;
        item.appendChild(term);
        item.appendChild(description);
        elements['experiment-health'].appendChild(item);
      }
      setControls(run);
    }

    function appendActivity(rows) {
      for (const activity of rows) {
        const model = view.activityView(activity);
        const item = documentRef.createElement('li');
        item.className = `activity-${model.level}`;
        const heading = documentRef.createElement('strong');
        const body = documentRef.createElement('span');
        heading.textContent = `${model.component} / ${model.event || 'activity'}`;
        body.textContent = model.summary;
        item.appendChild(heading);
        item.appendChild(body);
        elements['experiment-activity'].appendChild(item);
        after = Math.max(after, model.sequence);
      }
      while (elements['experiment-activity'].children.length > 1000) {
        elements['experiment-activity'].removeChild(elements['experiment-activity'].firstChild);
      }
    }

    async function loadModels() {
      const payload = await jsonRequest(fetchImpl, `${BASE}/models`);
      renderModels(payload.models || []);
      setControls(currentRun);
    }

    async function loadActivity(run) {
      if (!run) return;
      const payload = await jsonRequest(fetchImpl, `${BASE}/runs/${encodeURIComponent(run.runId)}/logs?after=${after}&limit=200`);
      appendActivity(payload.activity || []);
      after = Math.max(after, Number(payload.nextAfter) || 0);
    }

    async function refresh() {
      if (disposed || requestPending || refreshing) return;
      refreshing = true;
      try {
        const payload = await jsonRequest(fetchImpl, `${BASE}/runs/latest`);
        const run = payload.run || null;
        if (run?.runId !== currentRun?.runId) {
          after = 0;
          elements['experiment-activity'].replaceChildren();
        }
        renderRun(run);
        await loadActivity(run);
      } catch (error) {
        setStatus(error.message, true);
      } finally {
        refreshing = false;
      }
    }

    async function start() {
      if (requestPending || view.isActiveExperiment(currentRun)) return;
      requestPending = true;
      setControls(currentRun);
      const body = {
        startingModelId: elements['experiment-model'].value,
        retrievalBackend: elements['experiment-retrieval'].value,
        creatorCount: Number(elements['experiment-creators'].value),
        scenario: elements['experiment-scenario'].value,
        eventBudgetPerMonth: Number(elements['experiment-budget'].value),
        runSeed: Number(elements['experiment-seed'].value),
      };
      try {
        const payload = await startExperiment(fetchImpl, nonce, body);
        after = 0;
        elements['experiment-activity'].replaceChildren();
        renderRun(payload.run);
      } catch (error) {
        setStatus(error.message, true);
      } finally {
        requestPending = false;
        setControls(currentRun);
        await refresh();
      }
    }

    async function gracefulStop() {
      if (requestPending || !currentRun || !view.isActiveExperiment(currentRun)) return;
      requestPending = true;
      setControls(currentRun);
      try {
        const payload = await stopExperiment(fetchImpl, nonce, currentRun.runId);
        renderRun({ ...currentRun, status: payload.status });
      } catch (error) {
        setStatus(error.message, true);
      } finally {
        requestPending = false;
        setControls(currentRun);
        await refresh();
      }
    }

    const defaults = view.launchDefaults();
    elements['experiment-retrieval'].value = defaults.retrievalBackend;
    elements['experiment-scenario'].value = defaults.scenario;
    elements['experiment-creators'].value = defaults.creatorCount;
    elements['experiment-budget'].value = defaults.eventBudgetPerMonth;
    elements['experiment-seed'].value = defaults.runSeed;
    elements['experiment-start'].addEventListener('click', start);
    elements['experiment-stop'].addEventListener('click', gracefulStop);

    function initialize() {
      return Promise.all([loadModels(), refresh()]).catch((error) => setStatus(error.message, true));
    }
    function stop() {
      disposed = true;
      if (pollTimer !== null) clearIntervalImpl(pollTimer);
      pollTimer = null;
    }
    return { gracefulStop, initialize, refresh, start, stop };
  }

  function initializeExperimentDashboard() {
    const controller = createExperimentController({ document: root.document });
    controller.initialize();
    root.addEventListener('beforeunload', controller.stop, { once: true });
    return controller;
  }

  if (root.document && root.document.readyState !== 'loading') initializeExperimentDashboard();
  else if (root.document) root.document.addEventListener('DOMContentLoaded', initializeExperimentDashboard, { once: true });

  return { BASE, createExperimentController, jsonRequest, startExperiment, stopExperiment };
}));

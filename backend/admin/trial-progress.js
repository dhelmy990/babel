(function attachTrialProgress(root, factory) {
  const api = factory();
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  root.BabelTrialProgress = api;
}(typeof globalThis !== 'undefined' ? globalThis : this, function createTrialProgress() {
  const BASE = '/admin/api/v1/performance';

  function number(value, fallback = 0) {
    const parsed = Number(value);
    return Number.isFinite(parsed) ? parsed : fallback;
  }

  function duration(seconds) {
    const total = Math.max(0, Math.round(number(seconds)));
    const hours = Math.floor(total / 3600);
    const minutes = Math.floor((total % 3600) / 60);
    const remainder = total % 60;
    return hours > 0 ? `${hours}h ${minutes}m` : `${minutes}m ${remainder}s`;
  }

  function progressView(snapshot) {
    const row = snapshot && typeof snapshot === 'object' ? snapshot : {};
    const target = Math.max(0, number(row.targetCreatedBabels));
    const created = Math.max(0, number(row.createdBabels));
    const rate = Math.max(0, number(row.recentRate));
    const telemetry = row.telemetry && typeof row.telemetry === 'object'
      ? row.telemetry : {};
    const workPhase = String(telemetry.conditionPhase || row.phase || 'pending');
    const conditionActive = ['scheduled', 'draining'].includes(workPhase)
      && number(row.requested) > 0;
    const remaining = conditionActive
      ? Math.max(0, number(row.requested) - number(row.completed))
      : Math.max(0, target - created);
    const eta = rate > 0 ? remaining / rate : 0;
    return {
      phase: String(row.phase || 'pending'),
      workPhase,
      condition: `${number(row.conditionIndex)}/${number(row.conditionCount, 9)}`,
      seeded: `${number(row.seededArticles)}/${number(row.targetSeededArticles)}`,
      created: `${created}/${target}`,
      indexed: `${number(row.indexedBabels)}/${target}`,
      requested: number(row.requested),
      submitted: number(telemetry.submitted, number(row.completed)),
      completed: number(row.completed),
      errors: number(telemetry.errors),
      inFlight: number(telemetry.inFlight),
      elapsed: duration(row.elapsedSeconds),
      rate: `${rate.toFixed(2)}/s`,
      eta: duration(eta),
      percent: conditionActive
        ? Math.min(100, Math.round((number(row.completed) / number(row.requested)) * 100))
        : target > 0 ? Math.min(100, Math.round((created / target) * 100)) : 0,
      draining: row.draining === true,
    };
  }

  function createTrialProgressPoller({ fetchImpl, render }) {
    async function poll(experimentId) {
      const response = await fetchImpl(`${BASE}/${encodeURIComponent(experimentId)}`, {
        method: 'GET', headers: { Accept: 'application/json' },
      });
      if (!response.ok) throw new Error(`Progress unavailable (${response.status})`);
      const payload = await response.json();
      const view = progressView(payload?.trial?.progress);
      render(view, payload?.trial || null);
      return view;
    }
    return Object.freeze({ poll });
  }

  return Object.freeze({ BASE, createTrialProgressPoller, progressView });
}));

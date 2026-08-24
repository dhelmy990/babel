(function attachSeedStatus(root, factory) {
  const api = factory();

  if (typeof module !== 'undefined' && module.exports) {
    module.exports = api;
  }

  root.BabelSeedStatus = api;
}(typeof globalThis !== 'undefined' ? globalThis : this, function createSeedStatus() {
  function count(value) {
    return Number.isFinite(value) ? Math.max(0, Math.floor(value)) : 0;
  }

  function escapeHtml(value) {
    return String(value == null ? '' : value)
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;')
      .replace(/'/g, '&#39;');
  }

  function seedErrorViewModel(errors) {
    if (!Array.isArray(errors)) return [];

    return errors.map((error) => {
      const article = String(error && (error.article || error.title || error.current_article) || 'Wikipedia article');
      const message = String(error && (error.message || error.error) || 'Import failed');
      return {
        article,
        message,
        escapedArticle: escapeHtml(article),
        escapedMessage: escapeHtml(message),
      };
    });
  }

  function seedViewModel(status) {
    const source = status || {};
    const total = count(source.total);
    const completed = count(source.completed);
    const skipped = count(source.skipped);
    const failed = count(source.failed);
    const state = String(source.state || 'not_started');
    const satisfied = Math.min(total, completed + skipped);
    const percent = total === 0 ? 0 : Math.floor((satisfied / total) * 100);
    const remaining = Math.max(0, total - completed - skipped - failed);
    const retryCount = Math.max(0, total - completed - skipped);
    const disabled = state === 'queued' || state === 'running';

    const view = {
      label: `Seed ${total} Babels`,
      disabled,
      percent,
      summary: 'No Wikipedia Babels imported',
    };

    switch (state) {
      case 'queued':
        view.label = 'Seed run queued';
        view.summary = 'Preparing Wikipedia imports';
        break;
      case 'running':
        view.label = `Seeding ${remaining} remaining`;
        view.summary = `${completed} of ${total} Wikipedia Babels imported`;
        break;
      case 'completed':
        view.label = 'Seed complete';
        view.summary = `${completed} imported; ${skipped} already present`;
        break;
      case 'completed_with_errors':
        view.label = `Retry ${retryCount} missing`;
        view.summary = `${completed} imported; ${retryCount} need retry`;
        break;
      case 'failed':
        view.label = `Retry ${retryCount} missing`;
        view.summary = `Seed run failed; ${retryCount} need retry`;
        break;
      case 'interrupted':
        view.label = `Resume ${retryCount} remaining`;
        view.summary = `Seed run interrupted; ${retryCount} remain`;
        break;
      case 'not_started':
      default:
        break;
    }

    return view;
  }

  function normalizeSeedStatus(status) {
    const states = new Set(['not_started', 'queued', 'running', 'completed', 'completed_with_errors', 'failed', 'interrupted']);
    if (!status || typeof status !== 'object' || !states.has(status.state)) return null;
    return {
      ...status,
      total: count(status.total),
      completed: count(status.completed),
      skipped: count(status.skipped),
      failed: count(status.failed),
    };
  }

  return { escapeHtml, normalizeSeedStatus, seedErrorViewModel, seedViewModel };
}));

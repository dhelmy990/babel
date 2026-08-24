(function profileSelectorModule(globalScope, factory) {
    const api = factory();
    if (typeof module !== 'undefined' && module.exports) module.exports = api;
    if (globalScope) globalScope.ProfileSelector = api;
}(typeof globalThis !== 'undefined' ? globalThis : this, () => {
    const DEFAULT_BACKEND_URL = 'http://127.0.0.1:8787';
    const DEFAULT_TIMEOUT_MS = 5000;
    const MAX_BODY_LENGTH = 2 * 1024 * 1024;
    const COLOR_PATTERN = /^#[0-9a-f]{3}(?:[0-9a-f]{3})?(?:[0-9a-f]{2})?$/i;

    function requireObject(value, label) {
        if (!value || typeof value !== 'object' || Array.isArray(value)) {
            throw new TypeError(`${label} must be an object`);
        }
        return value;
    }

    function requireString(value, label, allowEmpty = false) {
        if (typeof value !== 'string' || (!allowEmpty && value.trim() === '')) {
            throw new TypeError(`${label} must be a non-empty string`);
        }
        return value;
    }

    function validateProfile(profile, label = 'profile') {
        requireObject(profile, label);
        requireString(profile.id, `${label} id`);
        requireString(profile.displayName, `${label} displayName`);
        requireString(profile.color, `${label} color`);
        if (!COLOR_PATTERN.test(profile.color)) {
            throw new TypeError(`${label} color must be a hex color`);
        }
        if (!Number.isInteger(profile.order) || profile.order < 0) {
            throw new TypeError(`${label} order must be a non-negative integer`);
        }
        return profile;
    }

    function orderedProfiles(profiles) {
        if (!Array.isArray(profiles)) throw new TypeError('profiles must be an array');
        const ids = new Set();
        const orders = new Set();
        profiles.forEach((profile, index) => {
            validateProfile(profile, `profiles[${index}]`);
            if (ids.has(profile.id)) throw new TypeError('profile ids must be unique');
            if (orders.has(profile.order)) throw new TypeError('profile order values must be unique');
            ids.add(profile.id);
            orders.add(profile.order);
        });

        return profiles.slice().sort((left, right) => {
            const leftPersonal = left.displayName === 'Personal' ? 0 : 1;
            const rightPersonal = right.displayName === 'Personal' ? 0 : 1;
            return leftPersonal - rightPersonal || left.order - right.order;
        });
    }

    function toRendererGraph(graph) {
        requireObject(graph, 'profile graph');
        validateProfile(graph.profile);
        if (!Array.isArray(graph.babels)) throw new TypeError('babels must be an array');
        if (!Array.isArray(graph.edges)) throw new TypeError('edges must be an array');

        const ids = new Set();
        const babels = graph.babels.map((babel, index) => {
            requireObject(babel, `babels[${index}]`);
            const id = requireString(babel.id, `babels[${index}] id`);
            if (ids.has(id)) throw new TypeError('babel ids must be unique');
            ids.add(id);
            requireString(babel.title, `babels[${index}] title`);
            requireString(babel.contentHtml, `babels[${index}] contentHtml`, true);
            requireString(babel.color, `babels[${index}] color`);
            if (!COLOR_PATTERN.test(babel.color)) {
                throw new TypeError(`babels[${index}] color must be a hex color`);
            }
            if (!Number.isInteger(babel.contentRevision) || babel.contentRevision < 1) {
                throw new TypeError(`babels[${index}] contentRevision must be a positive integer`);
            }
            return {
                id,
                title: babel.title,
                description: babel.contentHtml,
                color: babel.color,
            };
        });

        const edgeIds = new Set();
        const edges = graph.edges.map((edge, index) => {
            requireObject(edge, `edges[${index}]`);
            const id = requireString(edge.id, `edges[${index}] id`);
            const source = requireString(edge.sourceId, `edges[${index}] sourceId`);
            const target = requireString(edge.targetId, `edges[${index}] targetId`);
            if (edgeIds.has(id)) throw new TypeError('edge ids must be unique');
            if (!ids.has(source) || !ids.has(target)) {
                throw new TypeError(`edges[${index}] endpoint does not exist`);
            }
            edgeIds.add(id);
            return { id, source, target };
        });

        return { babels, edges };
    }

    function clearTransientState(state) {
        state.selectedBabel = null;
        state.comparisonBabels = [];
        state.editingBabel = null;
        state.isCreating = false;
        state.selectedSimilarBabels = [];
        state.deleteWarningBabel = null;
        if (state.deleteWarningTimeout) clearTimeout(state.deleteWarningTimeout);
        state.deleteWarningTimeout = null;
    }

    function applyProfileGraph(state, graph) {
        requireObject(state, 'state');
        const mapped = toRendererGraph(graph);
        state.babels = mapped.babels;
        state.edges = mapped.edges;
        state.currentProfile = graph.profile;
        state.isReadOnlyProfile = true;
        clearTransientState(state);
        return mapped;
    }

    function canMutate(state) {
        return !state?.isReadOnlyProfile;
    }

    function profileGraphPath(profileId) {
        requireString(profileId, 'profile id');
        return `/api/v1/profiles/${encodeURIComponent(profileId)}/graph`;
    }

    function normalizeBackendUrl(baseUrl) {
        let parsed;
        try {
            parsed = new URL(baseUrl || DEFAULT_BACKEND_URL);
        } catch {
            throw new TypeError('BABEL_BACKEND_URL must be a local backend URL');
        }
        const isLoopback = parsed.hostname === '127.0.0.1'
            || parsed.hostname === 'localhost'
            || parsed.hostname === '[::1]';
        if (parsed.protocol !== 'http:' || !isLoopback || parsed.username || parsed.password
            || (parsed.pathname !== '/' && parsed.pathname !== '') || parsed.search || parsed.hash) {
            throw new TypeError('BABEL_BACKEND_URL must be a local backend URL');
        }
        return parsed.origin;
    }

    function isAllowedBackendPath(pathname) {
        return pathname === '/api/v1/profiles'
            || /^\/api\/v1\/profiles\/[^/]+\/graph$/.test(pathname);
    }

    function createBackendRequest(options = {}) {
        const baseUrl = normalizeBackendUrl(options.baseUrl || DEFAULT_BACKEND_URL);
        const fetchImpl = options.fetchImpl || globalThis.fetch;
        const timeoutMs = options.timeoutMs ?? DEFAULT_TIMEOUT_MS;
        const setTimeoutImpl = options.setTimeoutImpl || setTimeout;
        const clearTimeoutImpl = options.clearTimeoutImpl || clearTimeout;
        if (typeof fetchImpl !== 'function') throw new TypeError('fetch implementation is required');
        if (!Number.isInteger(timeoutMs) || timeoutMs < 1 || timeoutMs > 30000) {
            throw new TypeError('backend timeout must be between 1 and 30000 milliseconds');
        }

        return async function backendRequest(pathname) {
            if (typeof pathname !== 'string' || !isAllowedBackendPath(pathname)) {
                throw new TypeError('Backend request path is not allowed');
            }
            const controller = new AbortController();
            const timeout = setTimeoutImpl(() => controller.abort(), timeoutMs);
            let response;
            try {
                response = await fetchImpl(`${baseUrl}${pathname}`, {
                    method: 'GET',
                    headers: { Accept: 'application/json' },
                    signal: controller.signal,
                });
            } catch {
                if (controller.signal.aborted) throw new Error('Backend request timed out');
                throw new Error('Unable to connect to the Babel backend');
            }
            try {
                if (!response || typeof response.status !== 'number' || typeof response.text !== 'function') {
                    throw new Error('Backend returned an invalid response');
                }
                if (!response.ok) throw new Error(`Backend request failed (${response.status})`);
                const contentType = response.headers?.get?.('content-type') || '';
                if (!/^application\/json(?:\s*;|$)/i.test(contentType)) {
                    throw new Error('Backend returned an invalid JSON response');
                }
                let body;
                try {
                    body = await response.text();
                } catch {
                    if (controller.signal.aborted) throw new Error('Backend request timed out');
                    throw new Error('Backend returned an invalid response');
                }
                if (body.length > MAX_BODY_LENGTH) throw new Error('Backend response was too large');
                let data;
                try {
                    data = JSON.parse(body);
                } catch {
                    throw new Error('Backend returned an invalid JSON response');
                }
                requireObject(data, 'backend response');
                return data;
            } finally {
                clearTimeoutImpl(timeout);
            }
        };
    }

    return {
        DEFAULT_BACKEND_URL,
        applyProfileGraph,
        canMutate,
        clearTransientState,
        createBackendRequest,
        orderedProfiles,
        profileGraphPath,
        toRendererGraph,
    };
}));

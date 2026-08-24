(function profileSelectorModule(globalScope, factory) {
    const api = factory();
    if (typeof module !== 'undefined' && module.exports) module.exports = api;
    if (globalScope) globalScope.ProfileSelector = api;
}(typeof globalThis !== 'undefined' ? globalThis : this, () => {
    const DEFAULT_BACKEND_URL = 'http://127.0.0.1:8787';
    const DEFAULT_TIMEOUT_MS = 5000;
    const MAX_BODY_LENGTH = 64 * 1024 * 1024;
    const COLOR_PATTERN = /^#[0-9a-f]{3}(?:[0-9a-f]{3})?(?:[0-9a-f]{2})?$/i;
    const UUID_PATTERN = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/;
    const MAX_DISPLAY_NAME_LENGTH = 200;

    function requireObject(value, label) {
        if (!value || typeof value !== 'object' || Array.isArray(value)) {
            throw new TypeError(`${label} must be an object`);
        }
        return value;
    }

    function requireString(value, label, allowEmpty = false, maxLength = MAX_BODY_LENGTH) {
        if (typeof value !== 'string' || (!allowEmpty && value.trim() === '')) {
            throw new TypeError(`${label} must be a non-empty string`);
        }
        if (value.length > maxLength) throw new TypeError(`${label} is too long`);
        return value;
    }

    function requireUuid(value, label) {
        if (typeof value !== 'string' || !UUID_PATTERN.test(value)) {
            throw new TypeError(`${label} must be a canonical lowercase UUID`);
        }
        return value;
    }

    function escapeHtml(value) {
        return String(value).replace(/[&<>"']/g, (character) => ({
            '&': '&amp;',
            '<': '&lt;',
            '>': '&gt;',
            '"': '&quot;',
            "'": '&#39;',
        })[character]);
    }

    function decodeHtmlEntities(value) {
        const named = { amp: '&', lt: '<', gt: '>', quot: '"', apos: "'", nbsp: ' ' };
        return value.replace(/&(#(?:x[0-9a-f]+|\d+)|[a-z]+);/gi, (match, entity) => {
            if (entity[0] !== '#') return named[entity.toLowerCase()] ?? match;
            const hexadecimal = entity[1]?.toLowerCase() === 'x';
            const codePoint = Number.parseInt(entity.slice(hexadecimal ? 2 : 1), hexadecimal ? 16 : 10);
            try {
                return Number.isFinite(codePoint) ? String.fromCodePoint(codePoint) : match;
            } catch {
                return match;
            }
        });
    }

    function htmlToPlainText(html) {
        requireString(html, 'contentHtml', true);
        return decodeHtmlEntities(html
            .replace(/<(script|style)\b[^>]*>[\s\S]*?<\/\1\s*>/gi, '')
            .replace(/<br\s*\/?>/gi, '\n')
            .replace(/<\/(?:p|div|li|h[1-6]|blockquote|pre|tr)\s*>/gi, '\n')
            .replace(/<[^>]*>/g, ''))
            .replace(/\r/g, '')
            .replace(/[ \t]+\n/g, '\n')
            .replace(/\n{3,}/g, '\n\n')
            .trim();
    }

    function wheelDirection(deltaX, deltaY) {
        const primaryDelta = Math.abs(deltaY) >= Math.abs(deltaX) ? deltaY : deltaX;
        return primaryDelta === 0 ? 0 : (primaryDelta > 0 ? 1 : -1);
    }

    function validateProfile(profile, label = 'profile') {
        requireObject(profile, label);
        requireUuid(profile.id, `${label} id`);
        requireString(profile.displayName, `${label} displayName`, false, MAX_DISPLAY_NAME_LENGTH);
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
            const id = requireUuid(babel.id, `babels[${index}] id`);
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
            const id = requireUuid(edge.id, `edges[${index}] id`);
            const source = requireUuid(edge.sourceId, `edges[${index}] sourceId`);
            const target = requireUuid(edge.targetId, `edges[${index}] targetId`);
            if (edgeIds.has(id)) throw new TypeError('edge ids must be unique');
            if (!ids.has(source) || !ids.has(target)) {
                throw new TypeError(`edges[${index}] endpoint does not exist`);
            }
            if (source === target) throw new TypeError(`edges[${index}] must not be a self-loop`);
            edgeIds.add(id);
            return { id, source, target };
        });

        const adjacency = new Map(Array.from(ids, (id) => [id, []]));
        const indegree = new Map(Array.from(ids, (id) => [id, 0]));
        const edgePairs = new Set(edges.map((edge) => `${edge.source}>${edge.target}`));
        const dependencyEdges = edges.filter(
            (edge) => !edgePairs.has(`${edge.target}>${edge.source}`),
        );
        dependencyEdges.forEach((edge) => {
            adjacency.get(edge.source).push(edge.target);
            indegree.set(edge.target, indegree.get(edge.target) + 1);
        });
        const ready = Array.from(ids).filter((id) => indegree.get(id) === 0);
        let visitedCount = 0;
        while (ready.length > 0) {
            const id = ready.pop();
            visitedCount += 1;
            adjacency.get(id).forEach((target) => {
                const remaining = indegree.get(target) - 1;
                indegree.set(target, remaining);
                if (remaining === 0) ready.push(target);
            });
        }
        if (visitedCount !== ids.size) throw new TypeError('profile graph contains a cycle');

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
        requireUuid(profileId, 'profile id');
        return `/api/v1/profiles/${profileId}/graph`;
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
            try {
                let response;
                try {
                    response = await fetchImpl(`${baseUrl}${pathname}`, {
                        method: 'GET',
                        headers: { Accept: 'application/json' },
                        redirect: 'error',
                        signal: controller.signal,
                    });
                } catch {
                    if (controller.signal.aborted) throw new Error('Backend request timed out');
                    throw new Error('Unable to connect to the Babel backend');
                }
                if (!response || typeof response.status !== 'number'
                    || typeof response.url !== 'string'
                    || typeof response.body?.getReader !== 'function') {
                    throw new Error('Backend returned an invalid response');
                }
                let responseUrl;
                try {
                    responseUrl = new URL(response.url);
                } catch {
                    throw new Error('Backend returned an invalid response origin');
                }
                if (responseUrl.origin !== baseUrl) {
                    throw new Error('Backend returned a response from an unexpected origin');
                }
                if (!response.ok) throw new Error(`Backend request failed (${response.status})`);
                const contentType = response.headers?.get?.('content-type') || '';
                if (!/^application\/json(?:\s*;|$)/i.test(contentType)) {
                    throw new Error('Backend returned an invalid JSON response');
                }
                const contentLength = response.headers?.get?.('content-length');
                if (contentLength) {
                    if (!/^\d+$/.test(contentLength)) {
                        throw new Error('Backend returned an invalid response');
                    }
                    if (Number(contentLength) > MAX_BODY_LENGTH) {
                        throw new Error('Backend response was too large');
                    }
                }

                const reader = response.body.getReader();
                const chunks = [];
                let byteLength = 0;
                try {
                    while (true) {
                        const { done, value } = await reader.read();
                        if (done) break;
                        if (!(value instanceof Uint8Array)) {
                            throw new Error('Backend returned an invalid response');
                        }
                        byteLength += value.byteLength;
                        if (byteLength > MAX_BODY_LENGTH) {
                            try { await reader.cancel(); } catch { /* Ignore cancellation errors. */ }
                            throw new Error('Backend response was too large');
                        }
                        chunks.push(value);
                    }
                } catch (error) {
                    if (controller.signal.aborted) throw new Error('Backend request timed out');
                    if (error instanceof Error
                        && (error.message === 'Backend response was too large'
                            || error.message === 'Backend returned an invalid response')) {
                        throw error;
                    }
                    throw new Error('Backend returned an invalid response');
                }
                const bytes = new Uint8Array(byteLength);
                let offset = 0;
                chunks.forEach((chunk) => {
                    bytes.set(chunk, offset);
                    offset += chunk.byteLength;
                });
                let body;
                try {
                    body = new TextDecoder('utf-8', { fatal: true }).decode(bytes);
                } catch {
                    throw new Error('Backend returned an invalid response');
                }
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
        escapeHtml,
        htmlToPlainText,
        orderedProfiles,
        profileGraphPath,
        toRendererGraph,
        wheelDirection,
    };
}));

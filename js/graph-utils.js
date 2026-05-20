// ============================================
// GRAPH UTILITIES (DAG, Transitivity, Cycles)
// ============================================

const GraphUtils = {
    getDependencyEdges(edges) {
        const edgePairs = new Map();
        edges.forEach(edge => {
            const key = [edge.source, edge.target].sort().join('-');
            if (!edgePairs.has(key)) edgePairs.set(key, []);
            edgePairs.get(key).push(edge);
        });
        const dependencies = [];
        edgePairs.forEach(group => {
            if (group.length === 1) dependencies.push(group[0]);
        });
        return dependencies;
    },

    buildAdjacencyList(edges, excludeEdge = null) {
        const adj = new Map();
        edges.forEach(edge => {
            if (excludeEdge &&
                edge.source === excludeEdge.source &&
                edge.target === excludeEdge.target) return;
            if (!adj.has(edge.source)) adj.set(edge.source, []);
            adj.get(edge.source).push(edge.target);
        });
        return adj;
    },

    hasPath(sourceId, targetId, edges, excludeDirectEdge = false) {
        const dependencies = this.getDependencyEdges(edges);
        const adj = this.buildAdjacencyList(
            dependencies,
            excludeDirectEdge ? { source: sourceId, target: targetId } : null
        );
        const visited = new Set();
        const queue = [sourceId];
        while (queue.length > 0) {
            const current = queue.shift();
            if (current === targetId) return true;
            if (visited.has(current)) continue;
            visited.add(current);
            (adj.get(current) || []).forEach(n => { if (!visited.has(n)) queue.push(n); });
        }
        return false;
    },

    wouldCreateCycle(sourceId, targetId, edges) {
        const dependencies = this.getDependencyEdges(edges);
        const adj = this.buildAdjacencyList(dependencies);
        const visited = new Set();
        const queue = [targetId];
        while (queue.length > 0) {
            const current = queue.shift();
            if (current === sourceId) return true;
            if (visited.has(current)) continue;
            visited.add(current);
            (adj.get(current) || []).forEach(n => { if (!visited.has(n)) queue.push(n); });
        }
        return false;
    },

    // Returns edges to remove; does not mutate State
    pruneTransitiveEdges(edges) {
        const dependencies = this.getDependencyEdges(edges);
        return dependencies.filter(edge => this.hasPath(edge.source, edge.target, edges, true));
    },

    findCycleEdge(nodeId, visited, recursionStack, adj, path = []) {
        if (recursionStack.has(nodeId)) return path[path.length - 1];
        if (visited.has(nodeId)) return null;
        visited.add(nodeId);
        recursionStack.add(nodeId);
        for (const { target, edge } of (adj.get(nodeId) || [])) {
            path.push(edge);
            const result = this.findCycleEdge(target, visited, recursionStack, adj, path);
            if (result) return result;
            path.pop();
        }
        recursionStack.delete(nodeId);
        return null;
    },

    // Returns edges to remove; does not mutate State
    breakCycles(edges) {
        const toRemove = [];
        let workingEdges = [...edges];
        let hasChanges = true;

        while (hasChanges) {
            hasChanges = false;
            const dependencies = this.getDependencyEdges(workingEdges);
            const adj = new Map();
            dependencies.forEach(edge => {
                if (!adj.has(edge.source)) adj.set(edge.source, []);
                adj.get(edge.source).push({ target: edge.target, edge });
            });

            const visited = new Set();
            const recursionStack = new Set();

            for (const startEdge of dependencies) {
                const cycleEdge = this.findCycleEdge(startEdge.source, visited, recursionStack, adj);
                if (cycleEdge) {
                    toRemove.push(cycleEdge);
                    workingEdges = workingEdges.filter(
                        e => !(e.source === cycleEdge.source && e.target === cycleEdge.target)
                    );
                    hasChanges = true;
                    break;
                }
            }
        }
        return toRemove;
    },

    cleanGraphOnLoad() {
        const cycleEdges = this.breakCycles(State.edges);
        cycleEdges.forEach(e => State.removeEdge(e.source, e.target));

        const transitiveEdges = this.pruneTransitiveEdges(State.edges);
        transitiveEdges.forEach(e => State.removeEdge(e.source, e.target));

        if (cycleEdges.length > 0 || transitiveEdges.length > 0) {
            console.log(`Graph cleaned: ${cycleEdges.length} cycles, ${transitiveEdges.length} transitive`);
        }
    },

    identifyMutualEdges(edges) {
        edges.forEach(edge => {
            edge.isMutual = edges.some(e => e.source === edge.target && e.target === edge.source);
        });
        return edges;
    },

    getDagEdges(edges) {
        this.identifyMutualEdges(edges);
        return edges.filter(e => !e.isMutual);
    }
};

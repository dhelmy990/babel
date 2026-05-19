// ============================================
// GRAPH UTILITIES (DAG, Transitivity, Cycles)
// ============================================

const GraphUtils = {
    /**
     * Get only dependency edges (not associations/bidirectional)
     */
    getDependencyEdges() {
        const edgePairs = new Map();

        State.edges.forEach(edge => {
            const key = [edge.source, edge.target].sort().join('-');
            if (!edgePairs.has(key)) {
                edgePairs.set(key, []);
            }
            edgePairs.get(key).push(edge);
        });

        const dependencies = [];
        edgePairs.forEach((edges) => {
            if (edges.length === 1) {
                dependencies.push(edges[0]);
            }
        });

        return dependencies;
    },

    /**
     * Build adjacency list from edges
     */
    buildAdjacencyList(edges, excludeEdge = null) {
        const adj = new Map();
        edges.forEach(edge => {
            if (excludeEdge &&
                edge.source === excludeEdge.source &&
                edge.target === excludeEdge.target) {
                return;
            }
            if (!adj.has(edge.source)) {
                adj.set(edge.source, []);
            }
            adj.get(edge.source).push(edge.target);
        });
        return adj;
    },

    /**
     * Check if there's a path from source to target
     */
    hasPath(sourceId, targetId, excludeDirectEdge = false) {
        const dependencies = this.getDependencyEdges();
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
            const neighbors = adj.get(current) || [];
            neighbors.forEach(n => {
                if (!visited.has(n)) queue.push(n);
            });
        }

        return false;
    },

    /**
     * Check if adding edge would create a cycle
     */
    wouldCreateCycle(sourceId, targetId) {
        const dependencies = this.getDependencyEdges();
        const adj = this.buildAdjacencyList(dependencies);

        const visited = new Set();
        const queue = [targetId];

        while (queue.length > 0) {
            const current = queue.shift();
            if (current === sourceId) return true;
            if (visited.has(current)) continue;

            visited.add(current);
            const neighbors = adj.get(current) || [];
            neighbors.forEach(n => {
                if (!visited.has(n)) queue.push(n);
            });
        }

        return false;
    },

    /**
     * Find and remove transitive edges
     */
    pruneTransitiveEdges() {
        const dependencies = this.getDependencyEdges();
        const toRemove = [];

        dependencies.forEach(edge => {
            if (this.hasPath(edge.source, edge.target, true)) {
                toRemove.push(edge);
            }
        });

        toRemove.forEach(edge => {
            const index = State.edges.findIndex(
                e => e.source === edge.source && e.target === edge.target
            );
            if (index >= 0) {
                console.log(`Pruning transitive edge: ${edge.source} -> ${edge.target}`);
                State.edges.splice(index, 1);
            }
        });

        return toRemove.length;
    },

    /**
     * Find a cycle edge using DFS
     */
    findCycleEdge(nodeId, visited, recursionStack, adj, path = []) {
        if (recursionStack.has(nodeId)) {
            return path[path.length - 1];
        }
        if (visited.has(nodeId)) {
            return null;
        }

        visited.add(nodeId);
        recursionStack.add(nodeId);

        const neighbors = adj.get(nodeId) || [];
        for (const { target, edge } of neighbors) {
            path.push(edge);
            const result = this.findCycleEdge(target, visited, recursionStack, adj, path);
            if (result) return result;
            path.pop();
        }

        recursionStack.delete(nodeId);
        return null;
    },

    /**
     * Detect and break cycles in the graph
     */
    breakCycles() {
        let cyclesBroken = 0;
        let hasChanges = true;

        while (hasChanges) {
            hasChanges = false;
            const dependencies = this.getDependencyEdges();

            const adj = new Map();
            dependencies.forEach(edge => {
                if (!adj.has(edge.source)) {
                    adj.set(edge.source, []);
                }
                adj.get(edge.source).push({ target: edge.target, edge });
            });

            const visited = new Set();
            const recursionStack = new Set();

            for (const startEdge of dependencies) {
                const cycleEdge = this.findCycleEdge(
                    startEdge.source, visited, recursionStack, adj
                );
                if (cycleEdge) {
                    const index = State.edges.findIndex(
                        e => e.source === cycleEdge.source && e.target === cycleEdge.target
                    );
                    if (index >= 0) {
                        console.log(`Breaking cycle: ${cycleEdge.source} -> ${cycleEdge.target}`);
                        State.edges.splice(index, 1);
                        cyclesBroken++;
                        hasChanges = true;
                        break;
                    }
                }
            }
        }

        return cyclesBroken;
    },

    /**
     * Clean graph on load
     */
    cleanGraphOnLoad() {
        const cyclesBroken = this.breakCycles();
        const transitivePruned = this.pruneTransitiveEdges();

        if (cyclesBroken > 0 || transitivePruned > 0) {
            console.log(`Graph cleaned: ${cyclesBroken} cycles, ${transitivePruned} transitive`);
        }
    },

    /**
     * Identify mutual edges (associations)
     */
    identifyMutualEdges(edges) {
        edges.forEach(edge => {
            edge.isMutual = edges.some(
                e => e.source === edge.target && e.target === edge.source
            );
        });
        return edges;
    },

    /**
     * Get DAG-compatible edges (non-mutual only)
     */
    getDagEdges() {
        const validEdges = State.getValidEdges();
        this.identifyMutualEdges(validEdges);
        return validEdges.filter(e => !e.isMutual);
    }
};

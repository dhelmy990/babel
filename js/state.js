// ============================================
// STATE MANAGEMENT
// ============================================

const State = {
    // Core data
    babels: [],
    edges: [],
    currentProfile: null,
    isReadOnlyProfile: false,

    // Selection state
    selectedBabel: null,
    comparisonBabels: [],
    editingBabel: null,

    // UI state
    isCreating: false,
    creationStartTime: null,
    creationAnimationFrame: null,
    selectedColor: Config.colors.bright[0],
    selectedSimilarBabels: [],

    // Delete state
    deleteWarningBabel: null,
    deleteWarningTimeout: null,

    // Methods
    reset() {
        this.babels = [];
        this.edges = [];
        this.selectedBabel = null;
        this.comparisonBabels = [];
        this.editingBabel = null;
        this.isCreating = false;
        this.selectedSimilarBabels = [];
        this.deleteWarningBabel = null;
    },

    addBabel(babel) {
        if (this.isReadOnlyProfile) return false;
        this.babels.push(babel);
        return true;
    },

    removeBabel(id) {
        if (this.isReadOnlyProfile) return false;
        this.babels = this.babels.filter(b => b.id !== id);
        this.edges = this.edges.filter(e => e.source !== id && e.target !== id);
        return true;
    },

    getBabel(id) {
        return this.babels.find(b => b.id === id);
    },

    addEdge(source, target) {
        if (this.isReadOnlyProfile) return false;
        const id = `${source}-${target}`;
        if (!this.edges.find(e => e.id === id)) {
            this.edges.push({ source, target, id });
            return true;
        }
        return false;
    },

    removeEdge(source, target) {
        if (this.isReadOnlyProfile) return false;
        const index = this.edges.findIndex(e => e.source === source && e.target === target);
        if (index >= 0) {
            this.edges.splice(index, 1);
            return true;
        }
        return false;
    },

    hasEdge(source, target) {
        return this.edges.some(e => e.source === source && e.target === target);
    },

    getValidEdges() {
        const validNodeIds = new Set(this.babels.map(b => b.id));
        return this.edges.filter(e =>
            validNodeIds.has(e.source) && validNodeIds.has(e.target)
        );
    }
};

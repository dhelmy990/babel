// Minimal adapter for the shared edge animation; never browser persistence.
const State = {
    nodes: new Map(),
    getBabel(id) { return this.nodes.get(id); }
};

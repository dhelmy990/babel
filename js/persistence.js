// ============================================
// PERSISTENCE (LocalStorage)
// ============================================

const Persistence = {
    /**
     * Save graph to localStorage
     */
    save() {
        const data = {
            babels: State.babels,
            edges: State.edges
        };

        try {
            localStorage.setItem(Config.storage.key, JSON.stringify(data));
            console.log('Graph saved');
            return true;
        } catch (e) {
            console.error('Failed to save:', e);
            return false;
        }
    },

    /**
     * Load graph from localStorage
     */
    load() {
        const saved = localStorage.getItem(Config.storage.key);

        if (saved) {
            try {
                const data = JSON.parse(saved);
                State.babels = data.babels || [];
                State.edges = data.edges || [];
                console.log(`Loaded ${State.babels.length} babels, ${State.edges.length} edges`);
                return true;
            } catch (e) {
                console.error('Failed to load:', e);
                return false;
            }
        }
        return false;
    },

    /**
     * Export graph as JSON file
     */
    exportJSON() {
        const data = {
            babels: State.babels,
            edges: State.edges,
            exportedAt: new Date().toISOString()
        };

        const blob = new Blob([JSON.stringify(data, null, 2)], { type: 'application/json' });
        const url = URL.createObjectURL(blob);

        const a = document.createElement('a');
        a.href = url;
        a.download = `babel-graph-${Date.now()}.json`;
        a.click();

        URL.revokeObjectURL(url);
    },

    /**
     * Import graph from JSON file
     */
    importJSON(file) {
        return new Promise((resolve, reject) => {
            const reader = new FileReader();

            reader.onload = (e) => {
                try {
                    const data = JSON.parse(e.target.result);
                    State.babels = data.babels || [];
                    State.edges = data.edges || [];
                    resolve(true);
                } catch (err) {
                    reject(err);
                }
            };

            reader.onerror = reject;
            reader.readAsText(file);
        });
    },

    /**
     * Clear all data
     */
    clear() {
        localStorage.removeItem(Config.storage.key);
        State.reset();
    }
};

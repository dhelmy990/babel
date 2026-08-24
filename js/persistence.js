// ============================================
// PERSISTENCE
// ============================================

const Persistence = {
    get isElectron() {
        return typeof window !== 'undefined' && !!window.electronAPI;
    },

    async save() {
        if (State.isReadOnlyProfile) return false;
        const data = { babels: State.babels, edges: State.edges };
        try {
            if (this.isElectron) {
                await window.electronAPI.save(data);
            } else {
                localStorage.setItem(Config.storage.key, JSON.stringify(data));
            }
            return true;
        } catch (e) {
            console.error('Failed to save:', e);
            return false;
        }
    },

    async load() {
        return false;
    },

    async exportJSON() {
        const data = {
            babels: State.babels,
            edges: State.edges,
            exportedAt: new Date().toISOString()
        };
        if (this.isElectron) {
            await window.electronAPI.exportJSON(data);
            return;
        }
        const blob = new Blob([JSON.stringify(data, null, 2)], { type: 'application/json' });
        const url = URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = url;
        a.download = `babel-graph-${Date.now()}.json`;
        a.click();
        URL.revokeObjectURL(url);
    },

    async importJSON(file) {
        if (State.isReadOnlyProfile) return false;
        if (this.isElectron) {
            const result = await window.electronAPI.importJSON();
            if (!result.success || !result.data) return false;
            State.babels = result.data.babels || [];
            State.edges = result.data.edges || [];
            return true;
        }
        return new Promise((resolve, reject) => {
            const reader = new FileReader();
            reader.onload = (e) => {
                try {
                    const data = JSON.parse(e.target.result);
                    State.babels = data.babels || [];
                    State.edges = data.edges || [];
                    resolve(true);
                } catch (err) { reject(err); }
            };
            reader.onerror = reject;
            reader.readAsText(file);
        });
    },

    clear() {
        if (State.isReadOnlyProfile) return false;
        if (this.isElectron) {
            window.electronAPI.save({ babels: [], edges: [] });
        } else {
            localStorage.removeItem(Config.storage.key);
        }
        State.reset();
        return true;
    }
};

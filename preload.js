const { contextBridge, ipcRenderer } = require('electron');

contextBridge.exposeInMainWorld('electronAPI', {
    save: (data) => ipcRenderer.invoke('save-data', data),
    load: () => ipcRenderer.invoke('load-data'),
    exportJSON: (data) => ipcRenderer.invoke('export-json', data),
    importJSON: () => ipcRenderer.invoke('import-json')
});

const { contextBridge, ipcRenderer } = require('electron');

contextBridge.exposeInMainWorld('electronAPI', {
    listProfiles: () => ipcRenderer.invoke('profiles:list'),
    loadProfileGraph: (profileId) => ipcRenderer.invoke('profiles:graph', profileId)
});

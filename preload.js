const { contextBridge, ipcRenderer } = require('electron');

contextBridge.exposeInMainWorld('electronAPI', {
    save: (data) => ipcRenderer.invoke('save-data', data),
    load: () => ipcRenderer.invoke('load-data'),
    exportJSON: (data) => ipcRenderer.invoke('export-json', data),
    importJSON: () => ipcRenderer.invoke('import-json'),
    openFile: (filePath) => ipcRenderer.invoke('open-file', filePath),
    checkFileExists: (filePath) => ipcRenderer.invoke('check-file-exists', filePath),
    locateFile: (filters) => ipcRenderer.invoke('locate-file', filters),
    saveFile: (buffer, subdir, ext) => ipcRenderer.invoke('save-file', { buffer, subdir, ext })
});

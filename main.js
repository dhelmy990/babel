const { app, BrowserWindow, ipcMain, dialog } = require('electron');
const path = require('path');
const fs = require('fs');

function getDataPath() {
    return path.join(app.getPath('userData'), 'babel-graph.json');
}

function createWindow() {
    const win = new BrowserWindow({
        width: 1400,
        height: 900,
        backgroundColor: '#0a0a0a',
        webPreferences: {
            preload: path.join(__dirname, 'preload.js'),
            contextIsolation: true,
            nodeIntegration: false
        }
    });
    win.loadFile('index.html');
}

app.whenReady().then(createWindow);

app.on('window-all-closed', () => {
    if (process.platform !== 'darwin') app.quit();
});

app.on('activate', () => {
    if (BrowserWindow.getAllWindows().length === 0) createWindow();
});

ipcMain.handle('save-data', (event, data) => {
    try {
        fs.writeFileSync(getDataPath(), JSON.stringify(data), 'utf8');
        return { success: true };
    } catch (e) {
        return { success: false, error: e.message };
    }
});

function readJSON(filePath) {
    let raw = fs.readFileSync(filePath);
    if (raw[0] === 0xef && raw[1] === 0xbb && raw[2] === 0xbf) raw = raw.slice(3);
    return JSON.parse(raw.toString('utf8'));
}

ipcMain.handle('load-data', () => {
    const filePath = getDataPath();

    // Try userData first
    if (fs.existsSync(filePath)) {
        try {
            const data = readJSON(filePath);
            return { success: true, data };
        } catch (e) {
            console.warn('userData file corrupt, falling back to bootstrap:', e.message);
            fs.unlinkSync(filePath); // delete corrupt file so bootstrap can run
        }
    }

    // Bootstrap from babel-graph.json in app directory
    const bootstrap = path.join(__dirname, 'babel-graph.json');
    if (!fs.existsSync(bootstrap)) return { success: true, data: null };
    try {
        const data = readJSON(bootstrap);
        fs.writeFileSync(filePath, JSON.stringify(data), 'utf8');
        console.log('Bootstrapped from babel-graph.json');
        return { success: true, data };
    } catch (e) {
        console.error('Bootstrap file invalid:', e.message);
        return { success: true, data: null };
    }
});

ipcMain.handle('export-json', async (event, data) => {
    const { filePath, canceled } = await dialog.showSaveDialog({
        defaultPath: `babel-graph-${Date.now()}.json`,
        filters: [{ name: 'JSON', extensions: ['json'] }]
    });
    if (canceled || !filePath) return { success: false };
    try {
        fs.writeFileSync(filePath, JSON.stringify(data, null, 2), 'utf8');
        return { success: true };
    } catch (e) {
        return { success: false, error: e.message };
    }
});

ipcMain.handle('import-json', async () => {
    const { filePaths, canceled } = await dialog.showOpenDialog({
        filters: [{ name: 'JSON', extensions: ['json'] }],
        properties: ['openFile']
    });
    if (canceled || !filePaths.length) return { success: false };
    try {
        const raw = fs.readFileSync(filePaths[0], 'utf8');
        return { success: true, data: JSON.parse(raw) };
    } catch (e) {
        return { success: false, error: e.message };
    }
});

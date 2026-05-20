const { app, BrowserWindow, ipcMain, dialog, screen, protocol, net, shell } = require('electron');
const path = require('path');
const fs = require('fs');

// Must be called before app is ready
protocol.registerSchemesAsPrivileged([
    { scheme: 'local-file', privileges: { secure: true, standard: true, supportFetchAPI: true } }
]);

function getDataPath() {
    return path.join(app.getPath('userData'), 'babel-graph.json');
}

function createWindow() {
    const primaryDisplay = screen.getPrimaryDisplay();
    const { width, height } = primaryDisplay.workAreaSize;

    const win = new BrowserWindow({
        x: primaryDisplay.bounds.x,
        y: primaryDisplay.bounds.y,
        width: width,
        height: height,
        backgroundColor: '#0a0a0a',
        webPreferences: {
            preload: path.join(__dirname, 'preload.js'),
            contextIsolation: true,
            nodeIntegration: false
        }
    });
    win.loadFile('index.html');
}

app.whenReady().then(() => {
    protocol.handle('local-file', (request) => {
        const filePath = decodeURIComponent(request.url.slice('local-file://'.length));
        return net.fetch('file://' + filePath);
    });
    createWindow();
});

app.on('window-all-closed', () => {
    if (process.platform !== 'darwin') app.quit();
});

app.on('activate', () => {
    if (BrowserWindow.getAllWindows().length === 0) createWindow();
});

// ── Persistence ──────────────────────────────────────────────────────────────

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

    if (fs.existsSync(filePath)) {
        try {
            return { success: true, data: readJSON(filePath) };
        } catch (e) {
            console.warn('userData file corrupt, falling back to bootstrap:', e.message);
            fs.unlinkSync(filePath);
        }
    }

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

// ── File operations ───────────────────────────────────────────────────────────

ipcMain.handle('open-file', async (event, filePath) => {
    const err = await shell.openPath(filePath);
    return err === '' ? { success: true } : { success: false, error: err };
});

ipcMain.handle('check-file-exists', (event, filePath) => {
    return fs.existsSync(filePath);
});

ipcMain.handle('locate-file', async (event, filters) => {
    const { filePaths, canceled } = await dialog.showOpenDialog({
        filters: filters || [{ name: 'All Files', extensions: ['*'] }],
        properties: ['openFile']
    });
    if (canceled || !filePaths.length) return { success: false };
    return { success: true, path: filePaths[0] };
});

ipcMain.handle('save-file', (event, { buffer, subdir, ext }) => {
    try {
        const dir = path.join(app.getPath('userData'), subdir);
        if (!fs.existsSync(dir)) fs.mkdirSync(dir, { recursive: true });
        const filePath = path.join(dir, `${Date.now()}.${ext}`);
        fs.writeFileSync(filePath, Buffer.from(buffer));
        return { success: true, path: filePath };
    } catch (e) {
        return { success: false, error: e.message };
    }
});

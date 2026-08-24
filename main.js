const { app, BrowserWindow, ipcMain, screen, shell } = require('electron');
const path = require('path');
const {
    createBackendRequest,
    profileGraphPath,
} = require('./js/profile-selector.js');
const {
    externalHttpsUrl,
    isTrustedRendererEvent,
    rendererUrl,
} = require('./js/electron-security.js');

// GPU stability flags — must be set before app is ready.
// These prevent the GPU sandbox from treating transient failures as fatal,
// and stop Electron from refusing to restart the GPU process after crashes.
app.commandLine.appendSwitch('disable-gpu-process-crash-limit');
app.commandLine.appendSwitch('ignore-gpu-blocklist');
// If the GPU process keeps crashing, fall back to SwiftShader (software WebGL)
// rather than leaving the renderer with a permanently dead context.
app.commandLine.appendSwitch('enable-unsafe-swiftshader');

const expectedRendererUrl = rendererUrl(__dirname);

function openExternalHttps(url) {
    const safeUrl = externalHttpsUrl(url);
    if (safeUrl) void shell.openExternal(safeUrl).catch(() => {});
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
            nodeIntegration: false,
            sandbox: true,
            webSecurity: true,
        }
    });
    win.webContents.setWindowOpenHandler(({ url }) => {
        openExternalHttps(url);
        return { action: 'deny' };
    });
    win.webContents.on('will-navigate', (event, url) => {
        if (url === expectedRendererUrl) return;
        event.preventDefault();
        openExternalHttps(url);
    });
    win.loadFile('index.html');
}

app.whenReady().then(() => {
    createWindow();
});

app.on('window-all-closed', () => {
    if (process.platform !== 'darwin') app.quit();
});

app.on('activate', () => {
    if (BrowserWindow.getAllWindows().length === 0) createWindow();
});

// ── Backend profile queries ─────────────────────────────────────────────────

let requestBackend;

async function backendRequest(pathname) {
    if (!requestBackend) {
        requestBackend = createBackendRequest({
            baseUrl: process.env.BABEL_BACKEND_URL || undefined,
        });
    }
    return requestBackend(pathname);
}

async function profileResult(request) {
    try {
        return { success: true, data: await request() };
    } catch (error) {
        return {
            success: false,
            error: error instanceof Error ? error.message : 'Backend request failed',
        };
    }
}

function trustedProfileHandler(handler) {
    return (event, ...args) => {
        if (!isTrustedRendererEvent(event, expectedRendererUrl)) {
            return { success: false, error: 'Profile request came from an untrusted renderer' };
        }
        return handler(...args);
    };
}

ipcMain.handle('profiles:list', trustedProfileHandler(() => profileResult(
    () => backendRequest('/api/v1/profiles'),
)));

ipcMain.handle('profiles:graph', trustedProfileHandler((profileId) => profileResult(
    () => backendRequest(profileGraphPath(profileId)),
)));

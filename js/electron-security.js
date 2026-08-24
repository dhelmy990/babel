const path = require('node:path');
const { pathToFileURL } = require('node:url');

function rendererUrl(appRoot) {
    return pathToFileURL(path.join(appRoot, 'index.html')).href;
}

function isTrustedRendererEvent(event, expectedUrl) {
    const frame = event?.senderFrame;
    return Boolean(frame
        && event?.sender?.mainFrame === frame
        && frame.url === expectedUrl);
}

function externalHttpsUrl(value) {
    let parsed;
    try {
        parsed = new URL(value);
    } catch {
        return null;
    }
    if (parsed.protocol !== 'https:' || parsed.username || parsed.password) return null;
    return parsed.href;
}

module.exports = {
    externalHttpsUrl,
    isTrustedRendererEvent,
    rendererUrl,
};

const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

const {
  externalHttpsUrl,
  isTrustedRendererEvent,
  rendererUrl,
} = require('../../js/electron-security.js');

test('IPC accepts only the main frame at the packaged renderer URL', () => {
  const expected = rendererUrl('/opt/babel');
  const mainFrame = { url: expected };
  const sender = { mainFrame };

  assert.equal(isTrustedRendererEvent({ sender, senderFrame: mainFrame }, expected), true);
  assert.equal(isTrustedRendererEvent({ sender, senderFrame: { url: expected } }, expected), false);
  assert.equal(isTrustedRendererEvent({
    sender: { mainFrame: { url: 'https://attacker.test/' } },
    senderFrame: { url: 'https://attacker.test/' },
  }, expected), false);
  assert.equal(isTrustedRendererEvent({}, expected), false);
});

test('external navigation permits only credential-free HTTPS URLs', () => {
  assert.equal(externalHttpsUrl('https://example.org/path?q=1'), 'https://example.org/path?q=1');
  for (const value of [
    'http://example.org/',
    'file:///etc/passwd',
    'javascript:alert(1)',
    'https://user:password@example.org/',
    'not a URL',
  ]) {
    assert.equal(externalHttpsUrl(value), null);
  }
});

test('Electron entry point enables sandboxing and guards IPC and navigation', () => {
  const source = fs.readFileSync(path.join(__dirname, '../../main.js'), 'utf8');
  assert.match(source, /sandbox:\s*true/);
  assert.match(source, /setWindowOpenHandler/);
  assert.match(source, /will-navigate/);
  assert.match(source, /isTrustedRendererEvent/);
  assert.match(source, /externalHttpsUrl/);
});

test('renderer uses local dependencies under a restrictive CSP', () => {
  const html = fs.readFileSync(path.join(__dirname, '../../index.html'), 'utf8');
  assert.match(html, /Content-Security-Policy/);
  assert.doesNotMatch(html, /https?:\/\//);
  assert.match(html, /node_modules\/three/);
  assert.match(html, /node_modules\/quill/);
});

test('profile UI escapes tooltip labels, maps comparison HTML, and blocks duplicate requests', () => {
  const appSource = fs.readFileSync(path.join(__dirname, '../../js/app.js'), 'utf8');
  const uiSource = fs.readFileSync(path.join(__dirname, '../../js/ui.js'), 'utf8');
  assert.match(appSource, /nodeLabel\(node\s*=>\s*ProfileSelector\.escapeHtml/);
  assert.match(uiSource, /ProfileSelector\.htmlToPlainText/);
  assert.match(uiSource, /profileRequestPending/);
  assert.match(uiSource, /aria-activedescendant/);
});

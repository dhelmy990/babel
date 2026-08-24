const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { spawnSync } = require('node:child_process');
const test = require('node:test');

const root = path.resolve(__dirname, '..', '..');
const justfile = fs.readFileSync(path.join(root, 'Justfile'), 'utf8');

test('Personal migration passes its source as a positional argument without interpolation', () => {
    assert.match(justfile, /set positional-arguments := true/);
    assert.match(justfile, /migrate-personal --source "\$1"/);
    assert.doesNotMatch(justfile, /\{\{source\}\}/);

    const marker = `babel-just-injection-${process.pid}`;
    const malicious = `\"; touch /tmp/${marker}; #`;
    const dryRun = spawnSync('just', ['--dry-run', 'migrate-personal', malicious], {
        cwd: root,
        encoding: 'utf8',
    });
    assert.equal(dryRun.status, 0, dryRun.stderr);
    assert.doesNotMatch(`${dryRun.stdout}${dryRun.stderr}`, new RegExp(marker));
    assert.equal(fs.existsSync(`/tmp/${marker}`), false);
});

test('start readiness is tied to the launched child identity and liveness', () => {
    assert.match(justfile, /BABEL_INSTANCE_TOKEN="\$instance_token" "\$backend" serve/);
    assert.match(justfile, /instanceToken/);
    const livenessChecks = justfile.match(/kill -0 "\$backend_pid"/g) || [];
    assert.ok(livenessChecks.length >= 2);
    assert.doesNotMatch(justfile.match(/start:[\s\S]*/)[0], /docker compose|babel_backend seed/);
});

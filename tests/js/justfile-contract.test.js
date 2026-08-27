const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { spawnSync } = require('node:child_process');
const test = require('node:test');

const root = path.resolve(__dirname, '..', '..');
const justfile = fs.readFileSync(path.join(root, 'Justfile'), 'utf8');
const compose = fs.readFileSync(path.join(root, 'compose.yaml'), 'utf8');
const onlineRunbook = fs.readFileSync(
    path.join(root, 'docs/runbooks/online-experiment.md'), 'utf8');

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

test('every checked-in split trainer launch explicitly preserves activation', () => {
    const activationCommandSource = String.raw`babel-online-trainer --run-id (?:\{run_id\}|<run>) --activation-enabled true`;
    const activationCommand = new RegExp(activationCommandSource);
    assert.match(compose, activationCommand);
    const splitRecipes = justfile.match(/online-split:[\s\S]*?(?=\nonline-monolith:)/)[0];
    assert.equal([...splitRecipes.matchAll(new RegExp(activationCommandSource, 'g'))].length, 2);
    assert.match(onlineRunbook, activationCommand);
});

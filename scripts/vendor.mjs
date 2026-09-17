// Reproduce browser assets from npm ci's exact package-lock.json versions.
import { copyFile, mkdir, readFile, readdir, writeFile } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';
import path from 'node:path';
import { build } from 'esbuild';

const root = fileURLToPath(new URL('../', import.meta.url));
const destination = path.join(root, 'study/static/vendor');
await mkdir(destination, { recursive: true });
await build({
  entryPoints: [path.join(root, 'scripts/editor-vendor.js')],
  outfile: path.join(destination, 'article-editor.js'),
  bundle: true, minify: true, format: 'esm', target: ['es2022'],
  legalComments: 'inline',
});
const packages = [
  ['three', '0.160.0', 'build/three.min.js', 'three.min.js'],
  ['d3', '7.9.0', 'dist/d3.min.js', 'd3.min.js'],
  ['3d-force-graph', '1.73.0', 'dist/3d-force-graph.min.js', '3d-force-graph.min.js'],
];
const licenses = [];
for (const [name, version, source, filename] of packages) {
  const directory = path.join(root, 'node_modules', name);
  const metadata = JSON.parse(await readFile(path.join(directory, 'package.json'), 'utf8'));
  if (metadata.version !== version) throw new Error(`Expected ${name}@${version}; run npm ci.`);
  await copyFile(path.join(directory, source), path.join(destination, filename));
  licenses.push(`${name}@${version}\nSource: https://registry.npmjs.org/${name}/-/${name}-${version}.tgz\n\n${await readFile(path.join(directory, 'LICENSE'), 'utf8')}`);
}
// Retain notices for all locked production dependencies included by the bundles.
const lock = JSON.parse(await readFile(path.join(root, 'package-lock.json'), 'utf8'));
for (const [directory, metadata] of Object.entries(lock.packages)) {
  if (!directory || metadata.dev || packages.some(([name]) => directory === `node_modules/${name}`)) continue;
  const files = await readdir(path.join(root, directory)).catch(error => {
    // esbuild locks binaries for every platform; npm installs only this host's.
    if (metadata.optional && error.code === 'ENOENT') return [];
    throw error;
  });
  const notices = files.filter(file => /^(license|copying|notice)(\.|$)/i.test(file));
  for (const notice of notices) {
    licenses.push(`${directory}@${metadata.version}\nSource: ${metadata.resolved}\n\n${await readFile(path.join(root, directory, notice), 'utf8')}`);
  }
}
await writeFile(path.join(destination, 'LICENSES.txt'), licenses.join('\n\n---\n\n'));

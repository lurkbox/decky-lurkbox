// Screenshots of the Quick Access panel and the go-live toast for the README, with made-up channels:
//   pnpm build && pnpm screenshots      → docs/screenshots/panel.png, docs/screenshots/toast.png
// It bundles scripts/screenshots/harness.js (React plus look-alikes of the @decky/ui pieces and a fake Decky
// backend) and renders the built dist/index.js in Electron. Electron comes from ELECTRON (a path), or the
// electron package wherever Node can find it (the Lurkbox monorepo has it). Runs under xvfb-run when installed.
import { spawnSync } from 'node:child_process';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { createRequire } from 'node:module';
import { fileURLToPath } from 'node:url';

const HERE = path.dirname(fileURLToPath(import.meta.url));
const ROOT = path.resolve(HERE, '..');
const OUT = path.join(ROOT, 'docs', 'screenshots');
const require = createRequire(import.meta.url);
const fail = (m) => { console.error(`[decky-screenshots] ${m}`); process.exit(1); };
const has = (cmd) => spawnSync('sh', ['-c', `command -v ${cmd}`]).status === 0;

function electronPath() {
  if (process.env.ELECTRON) return process.env.ELECTRON;
  for (const from of [ROOT, path.resolve(ROOT, '..')]) {
    try { return createRequire(path.join(from, 'package.json'))('electron'); } catch { /* next */ }
  }
  return fail('Electron not found: set ELECTRON=/path/to/electron');
}

async function bundle(tmp) {
  // Rollup and its plugins come with @decky/rollup (a devDependency).
  const deckyRollup = createRequire(require.resolve('@decky/rollup/package.json'));
  const { rollup } = await import(deckyRollup.resolve('rollup'));
  const load = async (n) => { const m = await import(deckyRollup.resolve(n)); return m.default || m; };
  const resolve = await load('@rollup/plugin-node-resolve');
  const commonjs = await load('@rollup/plugin-commonjs');
  const replace = await load('@rollup/plugin-replace');
  const b = await rollup({
    input: path.join(HERE, 'screenshots', 'harness.js'),
    plugins: [replace({ preventAssignment: true, values: { 'process.env.NODE_ENV': JSON.stringify('production') } }), resolve({ browser: true, rootDir: ROOT, modulePaths: [path.join(ROOT, 'node_modules')] }), commonjs()],
    onwarn: (w) => { if (w.code !== 'MODULE_LEVEL_DIRECTIVE') console.warn(w.message); },
  });
  await b.write({ file: path.join(tmp, 'harness.bundle.js'), format: 'iife' });
  await b.close();
}

async function main() {
  const dist = path.join(ROOT, 'dist', 'index.js');
  if (!fs.existsSync(dist)) fail('dist/index.js is missing: run `pnpm build` first');
  const tmp = fs.mkdtempSync(path.join(os.tmpdir(), 'lurkbox-decky-shots-'));
  await bundle(tmp);
  // The built plugin is an ES module; as a classic script it hands its default export to the harness.
  const plugin = fs.readFileSync(dist, 'utf8').replace(/^export \{ (\w+) as default \};\s*$/m, 'window.__plugin = $1;').replace(/\/\/# sourceMappingURL=.*$/m, '');
  if (!plugin.includes('window.__plugin =')) fail('unexpected dist/index.js export');
  fs.writeFileSync(path.join(tmp, 'plugin.js'), plugin);
  fs.writeFileSync(path.join(tmp, 'index.html'), fs.readFileSync(path.join(HERE, 'screenshots', 'harness.html'), 'utf8').replace('<script src="harness.bundle.js"></script>', '<script src="harness.bundle.js"></script>\n<script src="plugin.js"></script>'));
  fs.mkdirSync(OUT, { recursive: true });
  const electron = electronPath();
  const xvfb = process.env.SHOT_XVFB !== '0' && has('xvfb-run');
  const env = { ...process.env };
  delete env.ELECTRON_RUN_AS_NODE;
  if (xvfb) delete env.WAYLAND_DISPLAY;
  for (const [mode, w, hgt] of [['panel', 420, 758], ['toast', 440, 112]]) {
    const out = path.join(OUT, `${mode}.png`);
    const args = [path.join(HERE, 'screenshots', 'capture.cjs'), path.join(tmp, 'index.html'), mode, out, String(w), String(hgt)];
    const r = xvfb
      ? spawnSync('xvfb-run', ['-a', '-s', '-screen 0 1280x800x24', electron, '--ozone-platform=x11', ...args], { env, encoding: 'utf8', timeout: 60000 })
      : spawnSync(electron, args, { env, encoding: 'utf8', timeout: 60000 });
    const lines = `${r.stdout || ''}${r.stderr || ''}`.split('\n').filter((l) => l.startsWith('[decky-shot]') || l.startsWith('  '));
    console.log(lines.join('\n'));
    if (r.status !== 0) fail(`${mode} failed`);
    if (has('magick')) spawnSync('magick', [out, '-strip', '-define', 'png:compression-level=9', `PNG24:${out}`]);
    console.log(`  ${path.relative(ROOT, out)}  ${Math.round(fs.statSync(out).size / 1024)} KB`);
  }
  fs.rmSync(tmp, { recursive: true, force: true });
}

main().catch((e) => fail(e.stack || e.message));

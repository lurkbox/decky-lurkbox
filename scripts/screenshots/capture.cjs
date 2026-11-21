'use strict';
// Electron side of the Decky screenshots: opens the harness page at a fixed size, mounts the plugin, captures a PNG.
//   electron capture.cjs <harness.html> <panel|toast> <out.png> <width> <height>
const fs = require('fs');
const { app, BrowserWindow, session } = require('electron');

const [html, mode, out, w, h] = process.argv.slice(process.argv.findIndex((a) => a.endsWith('capture.cjs')) + 1).filter((a) => !a.startsWith('--'));
app.commandLine.appendSwitch('force-device-scale-factor', '1');
app.commandLine.appendSwitch('host-resolver-rules', 'MAP * ~NOTFOUND'); // nothing leaves the machine
const fail = (msg) => { console.error(`[decky-shot] ${msg}`); app.exit(1); };
setTimeout(() => fail('timed out'), 30000).unref();
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

app.whenReady().then(async () => {
  session.defaultSession.webRequest.onBeforeRequest((d, cb) => {
    const ok = /^(file|data|devtools):/.test(d.url);
    if (!ok) console.error(`[decky-shot] blocked ${d.url}`);
    cb({ cancel: !ok });
  });
  const win = new BrowserWindow({ width: Number(w), height: Number(h), useContentSize: true, show: false, webPreferences: { sandbox: true, contextIsolation: true } });
  const errors = [];
  win.webContents.on('console-message', (e) => { if (e.level === 'error') errors.push(e.message); if (process.env.SHOT_DEBUG) console.log(`[decky-shot] console: ${e.message}`); });
  await win.loadFile(html);
  const js = (c) => win.webContents.executeJavaScript(c);
  await js(`document.body.classList.add('mode-${mode}'); window.__mount(window.__plugin, ${JSON.stringify(mode)}); true`);
  for (let i = 0; i < 100 && !(await js("!!document.querySelector('.qam-field.hl') || !document.querySelector('.qam')")); i++) await sleep(50);
  if (mode === 'panel') {
    // Controller focus on the first live channel, as when the panel opens.
    await js("document.querySelector('.qam-field.hl').classList.add('gp-focus'); true");
  } else {
    await sleep(200);
    await js("const s = window.__fakeLive; window.__emit('lurkbox_live', { ...s, test: false }); true");
    for (let i = 0; i < 50 && !(await js("!!document.querySelector('.toast')")); i++) await sleep(50);
  }
  await js('document.fonts.ready.then(() => Promise.all([...document.images].map((i) => i.decode().catch(() => null)))).then(() => true)');
  await sleep(300);
  const img = await win.webContents.capturePage();
  fs.writeFileSync(out, img.toPNG());
  if (errors.length) console.log(`[decky-shot] page errors:\n  ${errors.join('\n  ')}`);
  console.log(`[decky-shot] ${out}`);
  app.exit(errors.length ? 1 : 0);
});

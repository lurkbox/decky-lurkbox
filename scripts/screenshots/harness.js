// Screenshot harness for the plugin's Quick Access panel and its go-live toast. Bundled by ../screenshots.mjs with
// React, then loaded in a page before the built dist/index.js. It stands in for everything the plugin gets from
// Steam and Decky Loader:
//   - SP_REACT / SP_JSX (Steam's React), from the react and react-dom devDependencies;
//   - DFL (@decky/ui): PanelSection, ButtonItem, ToggleField, DropdownItem, Field, TextField, ... drawn to look like
//     the Steam Quick Access menu (these are look-alikes, not Valve's components or assets);
//   - the Decky loader API (callable / events / toaster), answered by a fake backend with made-up channels.
// No network: avatars are drawn as SVG data URLs here.
import * as React from 'react';
import * as JSX from 'react/jsx-runtime';
import { createRoot } from 'react-dom/client';
import { FaBell, FaUserFriends, FaCog, FaBolt, FaMusic, FaQuestionCircle, FaPlug } from 'react-icons/fa';

const h = React.createElement;
window.SP_REACT = React;
window.SP_JSX = JSX;

// ---------------------------------------------------------------- made-up data

const NOW = Date.UTC(2026, 2, 14, 20, 0, 5); // the same pinned "now" as the app's demo mode
{
  const Real = Date;
  const offset = NOW - Real.now();
  class DemoDate extends Real {
    constructor(...a) { if (a.length) super(...a); else super(Real.now() + offset); }
    static now() { return Real.now() + offset; }
  }
  window.Date = DemoDate;
}

function avatar(seed, hue) {
  const s = 64;
  const svg = `<svg xmlns="http://www.w3.org/2000/svg" width="${s}" height="${s}" viewBox="0 0 64 64"><rect width="64" height="64" fill="hsl(${hue + 170} 40% 22%)"/><circle cx="32" cy="38" r="19" fill="hsl(${hue} 68% 62%)"/>${seed % 2 ? '<circle cx="20" cy="22" r="6" fill="hsl(' + hue + ' 60% 50%)"/><circle cx="44" cy="22" r="6" fill="hsl(' + hue + ' 60% 50%)"/>' : '<path d="M32 19 q3 -9 9 -8" stroke="hsl(' + hue + ' 60% 45%)" stroke-width="3" fill="none" stroke-linecap="round"/>'}<circle cx="26" cy="36" r="3" fill="#041012"/><circle cx="38" cy="36" r="3" fill="#041012"/><path d="M28 43 q4 4 8 0" stroke="#041012" stroke-width="2" fill="none" stroke-linecap="round"/></svg>`;
  return `data:image/svg+xml;charset=utf-8,${encodeURIComponent(svg)}`;
}

const LIVE = [
  ['pixelmoth', 'PixelMoth', 'Pixel Platformers', 'Blind run, world 6. No spoilers please!', 15420, 200],
  ['nightowlnoodles', 'NightOwlNoodles', 'Just Chatting', 'Ramen review night and your questions', 3310, 300],
  ['mossykeyboard', 'MossyKeyboard', 'Cozy Farming', 'Turnip season! Building the greenhouse', 2841, 110],
  ['lanternlynx', 'LanternLynx', 'Roguelike Dungeons', 'Daily seed, then viewer challenge runs', 2088, 30],
  ['orbitalotter', 'OrbitalOtter', 'Ocean Survival', 'Day 30 on the raft', 1736, 190],
  ['slowfoxruns', 'SlowFoxRuns', 'Retro Speedruns', 'Any% practice, trying for sub-25', 1207, 15],
  ['turbopancake', 'TurboPancake', 'Kart Racing', 'Cup races with chat', 978, 50],
].map(([login, name, game, title, viewers, hue], i) => ({
  stream_id: String(41000000000 + i), user_id: String(7100001 + i), login, name, game, title, viewers,
  started_at: new Date(NOW - (40 + i * 17) * 60000).toISOString(), avatar: avatar(i, hue),
}));

/** The channel the toast announces. */
window.__fakeLive = LIVE[0];

const state = {
  ok: true, signed_in: true, login: 'lurker', user_id: '1000001', signed_out_reason: '', device: null, device_error: '',
  settings: { enabled: true, interval: 60, source: 'bell', quiet: 'always', sound: 'steam', overrides: {} },
  live: LIVE, last_error: '', last_poll: NOW / 1000 - 20,
  bell: { available: true, matches: true, updated_at: new Date(NOW - 12 * 60000).toISOString(), count: 32, on: 21, login: 'lurker' },
};

const backend = {
  get_state: () => state,
  set_settings: (patch) => { Object.assign(state.settings, patch); return { ok: true, settings: state.settings }; },
  get_follows: () => ({ ok: true, channels: LIVE.map((l) => ({ id: l.user_id, login: l.login, name: l.name, bell: true, base: true, mode: '', notify: true })) }),
  set_channel: () => ({ ok: true }),
  clear_overrides: () => ({ ok: true }),
  open_channel: () => ({ ok: true }),
  refresh_now: () => ({ ok: true }),
  test_notification: () => ({ ok: true }),
  start_sign_in: () => ({ ok: true }),
  cancel_sign_in: () => ({ ok: true }),
  sign_out: () => ({ ok: true }),
};

// ---------------------------------------------------------------- Decky loader API

const events = new Map();
let pushToast = () => {};
window.__DECKY_SECRET_INTERNALS_DO_NOT_USE_OR_YOU_WILL_BE_FIRED_deckyLoaderAPIInit = {
  connect: () => ({
    _version: 2,
    callable: (name) => async (...args) => {
      if (!backend[name]) throw new Error(`no fake backend for ${name}`);
      return JSON.parse(JSON.stringify(backend[name](...args)));
    },
    addEventListener: (ev, fn) => { if (!events.has(ev)) events.set(ev, new Set()); events.get(ev).add(fn); return fn; },
    removeEventListener: (ev, fn) => { if (events.has(ev)) events.get(ev).delete(fn); },
    toaster: { toast: (t) => pushToast(t) },
  }),
};
/** Sends a backend event to the plugin, as Decky would. */
window.__emit = (ev, ...args) => { for (const fn of events.get(ev) || []) fn(...args); };

// ---------------------------------------------------------------- @decky/ui look-alikes

const cls = (...c) => c.filter(Boolean).join(' ');
const Focusable = ({ className, children, onClick, focused, ...rest }) => h('div', { className: cls(className, 'gp-focusable', focused && 'gp-focus'), tabIndex: 0, onClick, ...rest }, children);

function PanelSection({ title, children }) {
  return h('div', { className: 'qam-section' }, title ? h('div', { className: 'qam-section-title' }, title) : null, children);
}
const PanelSectionRow = ({ children }) => h('div', { className: 'qam-row' }, children);
function ButtonItem({ children, onClick, description, disabled }) {
  return h('div', { className: 'qam-field' },
    h('button', { className: 'qam-button gp-focusable', onClick, disabled }, children),
    description ? h('div', { className: 'qam-desc' }, description) : null);
}
function ToggleField({ label, description, checked, onChange }) {
  return h(Focusable, { className: 'qam-field qam-inline', onClick: () => onChange && onChange(!checked) },
    h('div', { className: 'qam-label-col' }, h('div', { className: 'qam-label' }, label), description ? h('div', { className: 'qam-desc' }, description) : null),
    h('div', { className: cls('qam-toggle', checked && 'on') }, h('div', { className: 'knob' })));
}
function DropdownItem({ label, description, rgOptions, selectedOption }) {
  const sel = (rgOptions || []).find((o) => o.data === selectedOption);
  return h('div', { className: 'qam-field' },
    h('div', { className: 'qam-label' }, label),
    h(Focusable, { className: 'qam-dropdown' }, h('span', {}, sel ? sel.label : ''), h('span', { className: 'chev' }, '▾')),
    description ? h('div', { className: 'qam-desc' }, description) : null);
}
function Field({ label, children, onClick, highlightOnFocus }) {
  return h(Focusable, { className: cls('qam-field', 'qam-inline', highlightOnFocus && 'hl'), onClick }, h('div', { className: 'qam-label-col' }, label), children);
}
function TextField({ label, value, onChange }) {
  return h('div', { className: 'qam-field' }, h('div', { className: 'qam-label' }, label), h('input', { className: 'qam-input gp-focusable', value, onChange }));
}

window.DFL = {
  PanelSection, PanelSectionRow, ButtonItem, ToggleField, DropdownItem, Field, TextField,
  Navigation: { CloseSideMenus() {}, Navigate() {} },
  Router: { MainRunningApp: null, RunningApps: [] },
  staticClasses: { Title: 'qam-plugin-title' },
};

// ---------------------------------------------------------------- the Quick Access frame and Steam-style toasts

function Toasts() {
  const [list, setList] = React.useState([]);
  pushToast = (t) => setList((l) => [...l, t]);
  return h('div', { className: 'toasts' }, list.map((t, i) => h('div', { key: i, className: 'toast' },
    h('div', { className: 'toast-logo' }, t.logo || null),
    h('div', { className: 'toast-text' },
      h('div', { className: 'toast-app' }, t.icon ? h('span', { className: 'toast-icon' }, t.icon) : null, 'LURKBOX'),
      h('div', { className: 'toast-title' }, t.title),
      h('div', { className: 'toast-body' }, t.body)))));
}

/** Mounts the plugin (definePlugin's result) in the Quick Access look-alike; mode 'panel' or 'toast'. */
window.__mount = (pluginFactory, mode) => {
  const plugin = pluginFactory();
  const tabs = [FaBell, FaUserFriends, FaCog, FaBolt, FaMusic, FaQuestionCircle, FaPlug];
  const qam = h('div', { className: 'qam' },
    h('div', { className: 'qam-content' },
      h('div', { className: 'qam-header' }, h('span', { className: 'qam-back' }, '←'), plugin.titleView),
      h('div', { className: 'qam-scroll' }, plugin.content)),
    h('div', { className: 'qam-tabs' }, tabs.map((t, i) => h('div', { key: i, className: cls('qam-tab', i === tabs.length - 1 && 'on') }, h(t)))));
  createRoot(document.getElementById('root')).render(h(React.Fragment, {}, mode === 'panel' ? qam : null, h(Toasts)));
};

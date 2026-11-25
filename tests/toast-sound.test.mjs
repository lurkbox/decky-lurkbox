import test from 'node:test';
import assert from 'node:assert/strict';
import { installToastMute, uninstallToastMute, muteToast } from '../src/toast-sound.ts';

// Steam's behaviour: the sound comes from a per-type table, whatever the toast says.
class Store {
  played = [];
  ChooseSound(info) { return info.sound; }
  PlayNotificationSound(n) { const s = this.ChooseSound({ sound: 6 }, n); if (s) this.played.push(n); }
}
const note = (data, decky = true) => ({ eType: 31, decky, data });

test('only marked toasts are silent', () => {
  const s = globalThis.NotificationStore = new Store();
  s.PlayNotificationSound(note(muteToast({ title: 'x' })));
  assert.equal(s.played.length, 0);
  s.PlayNotificationSound(note({ title: 'another plugin', playSound: false }));
  s.PlayNotificationSound({ eType: 2, data: undefined });
  assert.equal(s.played.length, 2);
  uninstallToastMute();
  assert.equal(Object.hasOwn(s, 'ChooseSound'), false);
});

test('survives another plugin replacing PlayNotificationSound, and a recreated store', () => {
  let s = globalThis.NotificationStore = new Store();
  muteToast({});
  const orig = Store.prototype.PlayNotificationSound;
  s.PlayNotificationSound = function (n) { return orig.call(this, n); };
  s.PlayNotificationSound(note(muteToast({})));
  assert.equal(s.played.length, 0);
  s = globalThis.NotificationStore = new Store();
  s.PlayNotificationSound(note(muteToast({})));
  assert.equal(s.played.length, 0);
  uninstallToastMute();
});

test('installs once, and is harmless without the hook', () => {
  const s = new Store();
  installToastMute(s); const f = s.ChooseSound;
  installToastMute(s); assert.equal(s.ChooseSound, f);
  uninstallToastMute();
  assert.equal(installToastMute({}), false);
  globalThis.NotificationStore = undefined;
  assert.equal(muteToast({}).playSound, false);
});

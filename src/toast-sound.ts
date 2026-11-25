// "Notification sound" off. Steam looks a notification's sound up in its own per-type table, so the `playSound` that
// Decky's toaster passes along is ignored. The choice is made in NotificationStore.ChooseSound(info, notification), which
// every PlayNotificationSound call goes through. It is wrapped here, and answers "no sound" only for toasts marked by
// muteToast(): every other notification, Steam's or another plugin's, keeps its sound.
// ChooseSound rather than PlayNotificationSound because other plugins (Steamcord) replace the latter, starting again from
// Steam's original each time, which would drop our wrapper.

const MARK = "lurkboxMuted";

type Choose = (this: unknown, info: unknown, notification: any) => unknown;

let wrapped: Choose | null = null;
let original: Choose | null = null;
let owner: any = null;
let hadOwn = false;

/** Wraps the store's ChooseSound if it isn't already (Steam may have recreated the store). False when Steam has none. */
export function installToastMute(store: any = (globalThis as any).NotificationStore): boolean {
  if (!store || typeof store.ChooseSound !== "function") return false;
  if (wrapped && owner === store && store.ChooseSound === wrapped) return true;
  const prev: Choose = store.ChooseSound;
  const own = Object.prototype.hasOwnProperty.call(store, "ChooseSound");
  const fn: Choose = function (info, notification) {
    try { if (notification?.decky && notification.data?.[MARK] === true) return null; } catch { /* Steam decides */ }
    return prev.call(this, info, notification);
  };
  try { store.ChooseSound = fn; } catch { return false; }
  if (store.ChooseSound !== fn) return false;
  wrapped = fn; original = prev; owner = store; hadOwn = own;
  return true;
}

/** Undoes the wrapper, unless someone else has wrapped it since (ours then only passes calls through). */
export function uninstallToastMute() {
  if (owner && wrapped && owner.ChooseSound === wrapped) {
    if (hadOwn) owner.ChooseSound = original;
    else delete owner.ChooseSound;
  }
  wrapped = original = owner = null;
}

/** Marks a toast as silent (and makes sure the wrapper is in place). */
export function muteToast<T extends object>(toast: T): T {
  if (!installToastMute()) console.warn("[Lurkbox] Steam's notification sound hook was not found; the toast may make a sound");
  return Object.assign(toast, { [MARK]: true, playSound: false });
}

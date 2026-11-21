// Finding and launching the Lurkbox shortcut, and the "quiet while playing" rule.
import { Router } from "@decky/ui";

export const LURKBOX_NAME = "Lurkbox";
// A known Lurkbox shortcut appid, used only when no shortcut named "Lurkbox" is found in the library.
export const FALLBACK_APPID = 3002582954;
const SHORTCUT_APP_TYPE = 1073741824;

export type QuietMode = "always" | "unless_lurkbox" | "quiet";

interface AppLike {
  appid: number;
  display_name: string;
  app_type?: number;
  gameid?: string;
}

/** A non-Steam shortcut's gameid: (appid << 32) | 0x02000000, as a decimal string. */
export function shortcutGameId(appid: number): string {
  return ((BigInt(appid >>> 0) << 32n) | 0x02000000n).toString();
}

function candidates(): AppLike[] {
  const w = window as any;
  const lists: AppLike[][] = [];
  try { lists.push(w.collectionStore?.deckDesktopApps?.allApps ?? []); } catch { /* not there */ }
  try { lists.push(w.collectionStore?.allAppsCollection?.allApps ?? []); } catch { /* not there */ }
  try { lists.push(w.appStore?.allApps ?? []); } catch { /* not there */ }
  return lists.flat();
}

/** The Lurkbox shortcut in the Steam library, by name (a shortcut preferred), or null. */
export function findLurkbox(): { appid: number; gameid: string } | null {
  let best: AppLike | null = null;
  for (const a of candidates()) {
    if (!a || a.display_name !== LURKBOX_NAME) continue;
    if (!best || (a.app_type === SHORTCUT_APP_TYPE && best.app_type !== SHORTCUT_APP_TYPE)) best = a;
  }
  if (!best) return null;
  const gameid = best.gameid && /^\d+$/.test(best.gameid) ? best.gameid : shortcutGameId(best.appid);
  return { appid: best.appid, gameid };
}

export function lurkboxAppId(): number {
  return findLurkbox()?.appid ?? FALLBACK_APPID;
}

/** Starts Lurkbox (if it is already running, Steam brings it forward). Returns false if Steam refused. */
export function launchLurkbox(): boolean {
  const found = findLurkbox();
  const gameid = found ? found.gameid : shortcutGameId(FALLBACK_APPID);
  try {
    SteamClient.Apps.RunGame(gameid, "", -1, 100);
    return true;
  } catch (e) {
    console.error("[Lurkbox] RunGame failed", e);
    return false;
  }
}

export function lurkboxRunning(): boolean {
  const id = lurkboxAppId();
  try { return (Router.RunningApps ?? []).some((a) => a.appid === id); } catch { return false; }
}

/**
 * Whether a go-live toast should show now.
 * - always: always.
 * - unless_lurkbox: not while Lurkbox is the app in front (you're already watching).
 * - quiet: not while any other game or app is running.
 */
export function allowedNow(mode: QuietMode): boolean {
  if (mode === "always") return true;
  const id = lurkboxAppId();
  try {
    if (mode === "unless_lurkbox") return Router.MainRunningApp?.appid !== id;
    return !(Router.RunningApps ?? []).some((a) => a.appid !== id);
  } catch {
    return true;
  }
}

import {
  ButtonItem,
  DropdownItem,
  Field,
  Navigation,
  PanelSection,
  PanelSectionRow,
  TextField,
  ToggleField,
  staticClasses,
} from "@decky/ui";
import { addEventListener, callable, definePlugin, removeEventListener, toaster } from "@decky/api";
import { useEffect, useMemo, useState } from "react";
import { FaTv } from "react-icons/fa";
import { QrCode } from "./qr";
import { QuietMode, allowedNow, launchLurkbox } from "./lurkbox";
import { muteToast, uninstallToastMute } from "./toast-sound";

// ---------------------------------------------------------------- backend calls (main.py)

interface LiveEvent {
  stream_id: string;
  user_id: string;
  login: string;
  name: string;
  game: string;
  title: string;
  viewers: number;
  started_at: string;
  avatar: string;
  test?: boolean;
}

type Source = "bell" | "all" | "list";

interface Settings {
  enabled: boolean;
  interval: number;
  source: Source;
  quiet: QuietMode;
  sound: boolean;
  overrides: Record<string, { login: string; mode: "mute" | "force" }>;
}

interface BellSummary {
  available: boolean;
  matches: boolean;
  updated_at: string | null;
  count: number;
  on: number;
  login?: string;
}

interface State {
  ok: boolean;
  error?: string;
  signed_in: boolean;
  login: string;
  user_id: string;
  signed_out_reason: string;
  device: { user_code: string; verification_uri: string; expires_at: number } | null;
  device_error: string;
  settings: Settings;
  live: LiveEvent[];
  last_error: string;
  last_poll: number | null;
  bell: BellSummary;
}

interface FollowRow {
  id: string;
  login: string;
  name: string;
  bell: boolean | null;
  base: boolean;
  mode: "" | "mute" | "force";
  notify: boolean;
}

interface Result {
  ok: boolean;
  error?: string;
}

const getState = callable<[], State>("get_state");
const startSignIn = callable<[], Result>("start_sign_in");
const cancelSignIn = callable<[], Result>("cancel_sign_in");
const signOut = callable<[], Result>("sign_out");
const setSettings = callable<[patch: Partial<Settings>], Result & { settings?: Settings }>("set_settings");
const getFollows = callable<[force: boolean], Result & { channels?: FollowRow[] }>("get_follows");
const setChannel = callable<[userId: string, login: string, want: boolean], Result & { channels?: FollowRow[] }>("set_channel");
const clearOverrides = callable<[], Result>("clear_overrides");
const openChannelCall = callable<[login: string, name: string], Result>("open_channel");
const refreshNow = callable<[], Result>("refresh_now");
const testNotification = callable<[], Result>("test_notification");

// ---------------------------------------------------------------- shared state (QAM and the toast handler)

let current: State | null = null;
const listeners = new Set<(s: State) => void>();

async function refreshState(): Promise<State | null> {
  try {
    const s = await getState();
    if (s && s.ok) {
      current = s;
      listeners.forEach((l) => l(s));
    }
    return current;
  } catch (e) {
    console.error("[Lurkbox] get_state failed", e);
    return current;
  }
}

async function openChannel(login: string, name: string) {
  try {
    const r = await openChannelCall(login, name);
    if (!r || !r.ok) {
      toaster.toast({ title: "Lurkbox", body: "Couldn't ask Lurkbox to open that channel." });
      return;
    }
    Navigation.CloseSideMenus();
    if (!launchLurkbox()) toaster.toast({ title: "Lurkbox", body: "Couldn't start Lurkbox. Is the shortcut in your library?" });
  } catch (e) {
    console.error("[Lurkbox] open failed", e);
  }
}

function toastBody(ev: LiveEvent): string {
  if (ev.game && ev.title) return `${ev.game} · ${ev.title}`;
  return ev.game || ev.title || "";
}

async function onLive(ev: LiveEvent) {
  try {
    const s = current?.settings ?? (await refreshState())?.settings;
    if (!ev.test && s) {
      if (!s.enabled) return;
      if (!allowedNow(s.quiet)) return;
    }
    const toast = {
      title: `${ev.name || ev.login} is live`,
      body: toastBody(ev),
      logo: ev.avatar ? <img src={ev.avatar} style={{ width: "100%", height: "100%", borderRadius: "50%", objectFit: "cover" }} /> : undefined,
      icon: <FaTv />,
      duration: 8000,
      onClick: () => { void openChannel(ev.login, ev.name); },
    };
    toaster.toast(s && s.sound === false ? muteToast(toast) : toast);
    void refreshState();
  } catch (e) {
    console.error("[Lurkbox] toast failed", e);
  }
}

// ---------------------------------------------------------------- QAM panel

function useLurkboxState(): State | null {
  const [s, setS] = useState<State | null>(current);
  useEffect(() => {
    listeners.add(setS);
    void refreshState();
    return () => { listeners.delete(setS); };
  }, []);
  // Poll quickly while a sign-in code is showing, slowly otherwise.
  useEffect(() => {
    const t = setInterval(() => { void refreshState(); }, s?.device ? 2000 : 15000);
    return () => clearInterval(t);
  }, [!!s?.device]);
  return s;
}

function ago(iso: string | null): string {
  if (!iso) return "";
  const m = Math.round((Date.now() - Date.parse(iso)) / 60000);
  if (!Number.isFinite(m)) return "";
  if (m < 1) return "just now";
  if (m < 60) return `${m} min ago`;
  const h = Math.round(m / 60);
  return h < 48 ? `${h} h ago` : `${Math.round(h / 24)} days ago`;
}

function bellDescription(s: State): string {
  const b = s.bell;
  if (!b.available) return "Lurkbox hasn't shared your Twitch notification settings yet (open Lurkbox signed in once). Until then: all followed channels.";
  if (!b.matches) return `Lurkbox's notification settings are for ${b.login || "another account"}. Until they match: all followed channels.`;
  return `From Lurkbox, ${ago(b.updated_at)}: ${b.on} of ${b.count} channels have notifications on.`;
}

function SignIn({ s }: { s: State }) {
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");
  if (s.device) {
    const short = s.device.verification_uri.replace(/^https?:\/\/(www\.)?/, "").replace(/\?.*$/, "");
    return (
      <PanelSection title="Sign in">
        <PanelSectionRow>
          <div style={{ textAlign: "center", width: "100%", padding: "0 16px", boxSizing: "border-box" }}>
            <div>On your phone, go to</div>
            <div style={{ fontWeight: "bold" }}>{short}</div>
            <div style={{ marginTop: 6 }}>and enter</div>
            <div style={{ fontSize: 34, fontWeight: "bold", letterSpacing: 4, margin: "6px 0" }}>{s.device.user_code}</div>
            <div style={{ fontSize: 12, opacity: 0.7 }}>or scan:</div>
            <QrCode text={s.device.verification_uri} size={150} />
          </div>
        </PanelSectionRow>
        <PanelSectionRow>
          <ButtonItem layout="below" onClick={async () => { await cancelSignIn(); void refreshState(); }}>Cancel</ButtonItem>
        </PanelSectionRow>
      </PanelSection>
    );
  }
  return (
    <PanelSection title="Sign in">
      <PanelSectionRow>
        <div style={{ padding: "0 16px", fontSize: 13 }}>
          {s.signed_out_reason || "Sign in with Twitch to get go-live notifications for the channels you follow."}
          {s.device_error ? <div style={{ color: "#ff8a8a", marginTop: 4 }}>{s.device_error}</div> : null}
          {err ? <div style={{ color: "#ff8a8a", marginTop: 4 }}>{err}</div> : null}
        </div>
      </PanelSectionRow>
      <PanelSectionRow>
        <ButtonItem
          layout="below"
          disabled={busy}
          onClick={async () => {
            setBusy(true);
            setErr("");
            const r = await startSignIn();
            if (!r.ok) setErr(`Couldn't start sign-in (${r.error || "network"}).`);
            setBusy(false);
            void refreshState();
          }}
        >
          Sign in with Twitch
        </ButtonItem>
      </PanelSectionRow>
    </PanelSection>
  );
}

function LiveNow({ s }: { s: State }) {
  const [all, setAll] = useState(false);
  const rows = all ? s.live : s.live.slice(0, 8);
  return (
    <PanelSection title={`Live now (${s.live.length})`}>
      {s.live.length === 0 ? (
        <PanelSectionRow>
          <div style={{ padding: "0 16px", fontSize: 13, opacity: 0.8 }}>{s.last_poll ? "Nobody you follow is live." : "Checking…"}</div>
        </PanelSectionRow>
      ) : null}
      {rows.map((ev) => (
        <PanelSectionRow key={ev.user_id}>
          <Field
            focusable
            highlightOnFocus
            bottomSeparator="none"
            onActivate={() => { void openChannel(ev.login, ev.name); }}
            onClick={() => { void openChannel(ev.login, ev.name); }}
            label={
              <div style={{ display: "flex", alignItems: "center", gap: 10, minWidth: 0 }}>
                {ev.avatar
                  ? <img src={ev.avatar} style={{ width: 32, height: 32, borderRadius: "50%", flex: "none" }} />
                  : <FaTv style={{ flex: "none" }} />}
                <div style={{ minWidth: 0 }}>
                  <div style={{ fontSize: 14, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{ev.name}</div>
                  <div style={{ fontSize: 12, opacity: 0.7, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                    {ev.game || "Live"} · {ev.viewers.toLocaleString()} viewers
                  </div>
                </div>
              </div>
            }
          />
        </PanelSectionRow>
      ))}
      {s.live.length > 8 ? (
        <PanelSectionRow>
          <ButtonItem layout="below" onClick={() => setAll(!all)}>{all ? "Show fewer" : `Show all ${s.live.length}`}</ButtonItem>
        </PanelSectionRow>
      ) : null}
    </PanelSection>
  );
}

function channelNote(r: FollowRow): string {
  if (r.mode === "mute") return "Muted here";
  if (r.mode === "force") return "Always notify (set here)";
  if (r.bell === false) return "Notifications off at Twitch";
  if (r.bell === true) return "Notifications on at Twitch";
  return "";
}

function Channels({ s }: { s: State }) {
  const [open, setOpen] = useState(false);
  const [rows, setRows] = useState<FollowRow[] | null>(null);
  const [filter, setFilter] = useState("");
  const [limit, setLimit] = useState(25);
  const [err, setErr] = useState("");

  const load = async (force: boolean) => {
    setErr("");
    const r = await getFollows(force);
    if (r.ok && r.channels) setRows(r.channels);
    else setErr(`Couldn't load your follows (${r.error || "network"}).`);
  };
  useEffect(() => { if (open) void load(false); }, [open, s.settings.source]);

  const shown = useMemo(() => {
    const q = filter.trim().toLowerCase();
    const list = (rows ?? []).filter((r) => !q || r.name.toLowerCase().includes(q) || r.login.includes(q));
    return list;
  }, [rows, filter]);
  const overrides = Object.keys(s.settings.overrides).length;

  return (
    <PanelSection title="Channels">
      <PanelSectionRow>
        <ButtonItem layout="below" onClick={() => setOpen(!open)} description={overrides ? `${overrides} set here` : undefined}>
          {open ? "Hide channel list" : "Choose channels…"}
        </ButtonItem>
      </PanelSectionRow>
      {open ? (
        <>
          <PanelSectionRow>
            <TextField label="Search" value={filter} onChange={(e) => { setFilter(e.target.value); setLimit(25); }} />
          </PanelSectionRow>
          {err ? <PanelSectionRow><div style={{ padding: "0 16px", color: "#ff8a8a", fontSize: 13 }}>{err}</div></PanelSectionRow> : null}
          {rows === null && !err ? <PanelSectionRow><div style={{ padding: "0 16px", fontSize: 13 }}>Loading…</div></PanelSectionRow> : null}
          {shown.slice(0, limit).map((r) => (
            <PanelSectionRow key={r.id}>
              <ToggleField
                label={r.name}
                description={channelNote(r)}
                checked={r.notify}
                onChange={async (want) => {
                  const res = await setChannel(r.id, r.login, want);
                  if (res.ok && res.channels) setRows(res.channels);
                  void refreshState();
                }}
              />
            </PanelSectionRow>
          ))}
          {shown.length > limit ? (
            <PanelSectionRow>
              <ButtonItem layout="below" onClick={() => setLimit(limit + 50)}>{`Show more (${shown.length - limit})`}</ButtonItem>
            </PanelSectionRow>
          ) : null}
          {overrides ? (
            <PanelSectionRow>
              <ButtonItem
                layout="below"
                onClick={async () => { await clearOverrides(); await refreshState(); await load(false); }}
              >
                Clear the choices made here
              </ButtonItem>
            </PanelSectionRow>
          ) : null}
          <PanelSectionRow>
            <ButtonItem layout="below" onClick={() => { setRows(null); void load(true); }}>Reload follows</ButtonItem>
          </PanelSectionRow>
        </>
      ) : null}
    </PanelSection>
  );
}

const SOURCE_OPTIONS = [
  { data: "bell", label: "Channels with notifications on at Twitch" },
  { data: "all", label: "All followed channels" },
  { data: "list", label: "Only my list" },
];
const QUIET_OPTIONS = [
  { data: "always", label: "Always notify" },
  { data: "unless_lurkbox", label: "Not while Lurkbox is in front" },
  { data: "quiet", label: "Stay quiet while a game runs" },
];
const INTERVAL_OPTIONS = [30, 60, 120].map((n) => ({ data: n, label: n === 60 ? "Every minute" : n === 120 ? "Every 2 minutes" : "Every 30 seconds" }));

function SettingsPanel({ s }: { s: State }) {
  const set = async (patch: Partial<Settings>) => {
    await setSettings(patch);
    await refreshState();
  };
  return (
    <PanelSection title="Notifications">
      <PanelSectionRow>
        <ToggleField label="Go-live notifications" checked={s.settings.enabled} onChange={(v) => { void set({ enabled: v }); }} />
      </PanelSectionRow>
      <PanelSectionRow>
        <DropdownItem
          label="Notify for"
          description={s.settings.source === "bell" ? bellDescription(s) : s.settings.source === "list" ? "Only channels switched on below." : undefined}
          rgOptions={SOURCE_OPTIONS}
          selectedOption={s.settings.source}
          onChange={(o) => { void set({ source: o.data as Source }); }}
        />
      </PanelSectionRow>
      <PanelSectionRow>
        <DropdownItem
          label="While playing"
          rgOptions={QUIET_OPTIONS}
          selectedOption={s.settings.quiet}
          onChange={(o) => { void set({ quiet: o.data as QuietMode }); }}
        />
      </PanelSectionRow>
      <PanelSectionRow>
        <ToggleField label="Notification sound" checked={s.settings.sound !== false} onChange={(v) => { void set({ sound: v }); }} />
      </PanelSectionRow>
      <PanelSectionRow>
        <DropdownItem
          label="Check"
          rgOptions={INTERVAL_OPTIONS}
          selectedOption={s.settings.interval}
          onChange={(o) => { void set({ interval: o.data as number }); }}
        />
      </PanelSectionRow>
      <PanelSectionRow>
        <ButtonItem layout="below" onClick={() => { void testNotification(); }}>Test notification</ButtonItem>
      </PanelSectionRow>
    </PanelSection>
  );
}

function Account({ s }: { s: State }) {
  return (
    <PanelSection title="Account">
      <PanelSectionRow>
        <div style={{ padding: "0 16px", fontSize: 13 }}>
          Signed in as <b>{s.login}</b>
          {s.last_error ? <div style={{ color: "#ffb36b", marginTop: 4 }}>Last check failed: {s.last_error}</div> : null}
        </div>
      </PanelSectionRow>
      <PanelSectionRow>
        <ButtonItem layout="below" onClick={() => { void refreshNow(); }}>Check now</ButtonItem>
      </PanelSectionRow>
      <PanelSectionRow>
        <ButtonItem layout="below" onClick={async () => { await signOut(); void refreshState(); }}>Sign out</ButtonItem>
      </PanelSectionRow>
    </PanelSection>
  );
}

function Content() {
  const s = useLurkboxState();
  if (!s) {
    return (
      <PanelSection>
        <PanelSectionRow><div>Loading…</div></PanelSectionRow>
      </PanelSection>
    );
  }
  if (!s.signed_in) return <SignIn s={s} />;
  return (
    <>
      <LiveNow s={s} />
      <SettingsPanel s={s} />
      <Channels s={s} />
      <Account s={s} />
    </>
  );
}

export default definePlugin(() => {
  const liveListener = addEventListener<[ev: LiveEvent]>("lurkbox_live", (ev) => { void onLive(ev); });
  const stateListener = addEventListener<[]>("lurkbox_state", () => { void refreshState(); });
  void refreshState();

  return {
    name: "Lurkbox",
    titleView: <div className={staticClasses.Title}>Lurkbox</div>,
    content: <Content />,
    icon: <FaTv />,
    onDismount() {
      removeEventListener("lurkbox_live", liveListener);
      removeEventListener("lurkbox_state", stateListener);
      uninstallToastMute();
    },
  };
});

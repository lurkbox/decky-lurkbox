"""Lurkbox Decky companion: the logic behind main.py, kept free of the `decky` module so it can be unit tested.

Pieces:
- JsonStore: small JSON files in the plugin's settings dir, written atomically with mode 0600.
- sanitize_settings: the plugin settings and their defaults.
- LiveTracker: diffs successive /helix/streams/followed results into "went live" events.
- Backoff: poll delay after 429s and network errors.
- should_notify / BellExport: which channels notify (Twitch's bell export from Lurkbox, all, or only a list).
- write_open_request: asks Lurkbox to open a channel (src/main/open-request.js consumes it).
- Twitch: device-code sign-in, token refresh and the Helix calls, over an injectable http function.

Nothing here logs tokens.
"""

import json
import os
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request

# Lurkbox's public Twitch Client ID, duplicated from the Lurkbox app (src/config.js DEFAULT_CLIENT_ID) so this plugin
# stands alone. It is a public client (device-code flow), so there is no secret.
CLIENT_ID = "4eb4571f6v3hnf1xdwwxxetk3uc5cx"
SCOPES = ["user:read:follows"]
ID_BASE = "https://id.twitch.tv/oauth2"
HELIX = "https://api.twitch.tv/helix"

INTERVALS = (30, 60, 120)
SOURCES = ("bell", "all", "list")
QUIET_MODES = ("always", "unless_lurkbox", "quiet")
DEFAULT_SETTINGS = {
    "enabled": True,
    "interval": 60,
    "source": "bell",
    "quiet": "always",
    "sound": True,
    # user_id -> {"login": str, "mode": "mute" | "force"}
    "overrides": {},
}

LOGIN_RE_CHARS = set("abcdefghijklmnopqrstuvwxyz0123456789_")


def valid_login(s):
    return isinstance(s, str) and 1 <= len(s) <= 25 and all(c in LOGIN_RE_CHARS for c in s.lower())


# ---------------------------------------------------------------- files


def atomic_write_json(path, data, mode=0o600, owner=None):
    """Write JSON to path via a temp file + rename, with the given mode. owner=(uid, gid) chowns it (root only)."""
    d = os.path.dirname(path)
    if d:
        os.makedirs(d, exist_ok=True)
    tmp = "%s.%d.tmp" % (path, os.getpid())
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, mode)
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(data, f, indent=1)
        os.chmod(tmp, mode)
        if owner is not None:
            try:
                os.chown(tmp, owner[0], owner[1])
            except OSError:
                pass
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def read_json(path, default=None):
    try:
        with open(path, "r") as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


class JsonStore:
    def __init__(self, path, default):
        self.path = path
        self.default = default
        data = read_json(path, None)
        self.data = data if isinstance(data, dict) else dict(default)

    def save(self):
        atomic_write_json(self.path, self.data)

    def replace(self, data):
        self.data = data
        self.save()


def sanitize_settings(raw):
    s = dict(DEFAULT_SETTINGS)
    s["overrides"] = {}
    if not isinstance(raw, dict):
        return s
    if isinstance(raw.get("enabled"), bool):
        s["enabled"] = raw["enabled"]
    try:
        iv = int(raw.get("interval", 60))
        if iv in INTERVALS:
            s["interval"] = iv
    except (TypeError, ValueError):
        pass
    if raw.get("source") in SOURCES:
        s["source"] = raw["source"]
    if raw.get("quiet") in QUIET_MODES:
        s["quiet"] = raw["quiet"]
    if isinstance(raw.get("sound"), bool):
        s["sound"] = raw["sound"]
    ov = raw.get("overrides")
    if isinstance(ov, dict):
        for uid, v in ov.items():
            if not (isinstance(uid, str) and uid.isdigit() and isinstance(v, dict)):
                continue
            if v.get("mode") not in ("mute", "force"):
                continue
            login = v.get("login") if valid_login(v.get("login")) else ""
            s["overrides"][uid] = {"login": login.lower(), "mode": v["mode"]}
    return s


# ---------------------------------------------------------------- go-live detection


class LiveTracker:
    """Turns successive lists of live followed streams into go-live events.

    - The first successful poll only records who is live (no burst of notifications at boot).
    - A channel is "new" when it wasn't live in the previous poll.
    - A stream_id is announced at most once, so a channel flapping offline/online on the same stream stays quiet.
    """

    MAX_SEEN = 2000

    def __init__(self):
        self.primed = False
        self.live = {}  # user_id -> stream dict
        self.seen = {}  # stream_id -> time first seen (insertion ordered)

    def reset(self):
        self.__init__()

    def update(self, streams, now=None):
        now = time.time() if now is None else now
        current = {}
        for s in streams:
            uid = str(s.get("user_id") or "")
            if uid:
                current[uid] = s
        fresh = []
        for uid, s in current.items():
            sid = str(s.get("id") or "")
            if self.primed and uid not in self.live and sid and sid not in self.seen:
                fresh.append(s)
            if sid and sid not in self.seen:
                self.seen[sid] = now
        while len(self.seen) > self.MAX_SEEN:
            self.seen.pop(next(iter(self.seen)))
        self.live = current
        self.primed = True
        return fresh


class Backoff:
    """Poll delay. Doubles from the interval on network/server errors (cap 15 min); waits for the reset on 429."""

    CAP = 900

    def __init__(self):
        self.failures = 0

    def ok(self, interval):
        self.failures = 0
        return interval

    def network(self, interval):
        self.failures += 1
        return min(self.CAP, interval * (2 ** min(self.failures, 10)))

    def rate_limited(self, interval, reset_at=None, now=None):
        self.failures += 1
        now = time.time() if now is None else now
        wait = 60
        if reset_at:
            try:
                wait = float(reset_at) - now
            except (TypeError, ValueError):
                wait = 60
        return max(interval, min(self.CAP, max(5, wait + 1)))


# ---------------------------------------------------------------- which channels notify


FLATPAK_ID = "io.github.lurkbox.Lurkbox"


def lurkbox_dirs(home):
    """Lurkbox's profile dirs: the AppImage's ~/.config/Lurkbox, then the Flatpak's ~/.var/app/<id>/config/Lurkbox."""
    return [os.path.join(home, ".config", "Lurkbox"), os.path.join(home, ".var", "app", FLATPAK_ID, "config", "Lurkbox")]


def pick_lurkbox_dir(dirs):
    """The profile in use: the one with the newest notify-channels.json, else the first that exists, else the first."""
    best, best_m = None, None
    for d in dirs:
        try:
            m = os.stat(os.path.join(d, "notify-channels.json")).st_mtime
        except OSError:
            continue
        if best_m is None or m > best_m:
            best, best_m = d, m
    if best is not None:
        return best
    for d in dirs:
        if os.path.isdir(d):
            return d
    return dirs[0]


class BellExport:
    """Lurkbox's notify-channels.json (src/main/notify-export.js). path: a path, or a function returning the current
    one (pick_lurkbox_dir: the AppImage's or the Flatpak's profile)."""

    def __init__(self, path):
        self._path = path
        self.mtime = None
        self.data = None
        self.loaded_from = None

    @property
    def path(self):
        return self._path() if callable(self._path) else self._path

    def load(self):
        p = self.path
        if p != self.loaded_from:
            self.mtime, self.data, self.loaded_from = None, None, p
        try:
            m = os.stat(p).st_mtime
        except OSError:
            self.mtime, self.data = None, None
            return None
        if m != self.mtime:
            d = read_json(p, None)
            self.data = d if isinstance(d, dict) and isinstance(d.get("channels"), list) else None
            self.mtime = m
        return self.data

    def for_user(self, user_id):
        """{user_id: bool} for the given user, or None if the export is missing or belongs to someone else."""
        d = self.load()
        if not d or str(d.get("user_id")) != str(user_id):
            return None
        out = {}
        for c in d["channels"]:
            if isinstance(c, dict) and str(c.get("id", "")).isdigit():
                out[str(c["id"])] = bool(c.get("notify"))
        return out

    def summary(self, user_id):
        d = self.load()
        if not d:
            return {"available": False, "matches": False, "updated_at": None, "count": 0, "on": 0}
        chans = [c for c in d.get("channels", []) if isinstance(c, dict)]
        return {
            "available": True,
            "matches": str(d.get("user_id")) == str(user_id),
            "updated_at": d.get("updated_at"),
            "count": len(chans),
            "on": sum(1 for c in chans if c.get("notify")),
            "login": d.get("login") or "",
        }


def base_notify(user_id, source, bell):
    """What the source alone says for a channel. bell = {user_id: bool} or None (no usable export)."""
    if source == "all":
        return True
    if source == "list":
        return False
    # bell: unknown channels (followed after the last export) default to on, as Twitch does for a new follow.
    # Without a usable export, fall back to all followed.
    if bell is None:
        return True
    return bell.get(str(user_id), True)


def should_notify(user_id, settings, bell):
    """Final decision: a mute always wins, a force-on always notifies, otherwise the source decides."""
    ov = settings.get("overrides", {}).get(str(user_id))
    if ov and ov.get("mode") == "mute":
        return False
    if ov and ov.get("mode") == "force":
        return True
    return base_notify(user_id, settings.get("source", "bell"), bell)


# ---------------------------------------------------------------- open requests for Lurkbox


def write_open_request(lurkbox_dir, channel, name=None, now=None, owner=None):
    """Writes open-request.json {channel, name, at(ms)} for Lurkbox to consume. Returns the path."""
    if not valid_login(channel):
        raise ValueError("bad channel")
    now = time.time() if now is None else now
    data = {"channel": channel.lower(), "at": int(now * 1000)}
    if isinstance(name, str) and 0 < len(name) <= 50:
        data["name"] = name
    if owner is not None and not os.path.isdir(lurkbox_dir):
        os.makedirs(lurkbox_dir, exist_ok=True)
        try:
            os.chown(lurkbox_dir, owner[0], owner[1])
        except OSError:
            pass
    path = os.path.join(lurkbox_dir, "open-request.json")
    atomic_write_json(path, data, owner=owner)
    return path


# ---------------------------------------------------------------- HTTP + Twitch


class HttpError(Exception):
    def __init__(self, status, body=None, headers=None):
        Exception.__init__(self, "http %s" % status)
        self.status = status
        self.body = body if isinstance(body, dict) else {}
        self.headers = headers or {}

    @property
    def message(self):
        return str(self.body.get("message") or "")


class NetworkError(Exception):
    pass


def _ssl_context():
    ctx = ssl.create_default_context()
    paths = ssl.get_default_verify_paths()
    if not (paths.cafile and os.path.exists(paths.cafile)) and not (paths.capath and os.path.isdir(paths.capath)):
        # Decky's bundled Python may not know the system CA store.
        for f in ("/etc/ssl/certs/ca-certificates.crt", "/etc/ca-certificates/extracted/tls-ca-bundle.pem", "/etc/pki/tls/certs/ca-bundle.crt"):
            if os.path.exists(f):
                ctx.load_verify_locations(cafile=f)
                break
    return ctx


_CTX = None


def urllib_http(method, url, headers=None, data=None, timeout=20):
    """Blocking HTTP. Returns (status, headers dict lower-cased, parsed JSON or None). Raises NetworkError."""
    global _CTX
    if _CTX is None:
        _CTX = _ssl_context()
    body = None
    if data is not None:
        body = urllib.parse.urlencode(data).encode()
    req = urllib.request.Request(url, data=body, method=method, headers=dict(headers or {}))
    if body is not None:
        req.add_header("Content-Type", "application/x-www-form-urlencoded")
    req.add_header("User-Agent", "Lurkbox-Decky/0.1")
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=_CTX) as r:
            raw = r.read()
            status, hdrs = r.status, {k.lower(): v for k, v in r.headers.items()}
    except urllib.error.HTTPError as e:
        raw = e.read() if hasattr(e, "read") else b""
        status, hdrs = e.code, {k.lower(): v for k, v in (e.headers or {}).items()}
    except (urllib.error.URLError, OSError, ValueError) as e:
        raise NetworkError(str(getattr(e, "reason", e))[:120])
    try:
        parsed = json.loads(raw.decode() or "null")
    except ValueError:
        parsed = None
    return status, hdrs, parsed


class Twitch:
    """Device-code sign-in and Helix calls. `http` is a blocking function like urllib_http; `run` runs it off the
    event loop (asyncio.to_thread in the plugin, a direct call in tests)."""

    def __init__(self, tokens, http=urllib_http, run=None, client_id=CLIENT_ID, log=None):
        self.tokens = tokens  # JsonStore
        self.http = http
        self.run = run
        self.client_id = client_id
        self.log = log or (lambda *_: None)

    async def _call(self, *args, **kw):
        if self.run:
            return await self.run(self.http, *args, **kw)
        return self.http(*args, **kw)

    # -- tokens

    @property
    def signed_in(self):
        t = self.tokens.data
        return bool(t.get("access_token") and t.get("user_id"))

    @property
    def user_id(self):
        return str(self.tokens.data.get("user_id") or "")

    @property
    def login(self):
        return str(self.tokens.data.get("login") or "")

    def clear(self):
        self.tokens.replace({})

    async def start_device(self):
        st, _h, b = await self._call("POST", ID_BASE + "/device", data={"client_id": self.client_id, "scopes": " ".join(SCOPES)})
        if st != 200 or not isinstance(b, dict) or not b.get("device_code"):
            raise HttpError(st, b)
        return {
            "device_code": b["device_code"],
            "user_code": b.get("user_code", ""),
            "verification_uri": b.get("verification_uri", "https://www.twitch.tv/activate"),
            "expires_at": time.time() + int(b.get("expires_in", 1800)),
            "interval": int(b.get("interval", 5)),
        }

    async def poll_device(self, device_code):
        """Returns 'pending', 'slow_down', or 'ok' (tokens saved). Raises HttpError when the flow failed."""
        st, _h, b = await self._call(
            "POST",
            ID_BASE + "/token",
            data={
                "client_id": self.client_id,
                "scopes": " ".join(SCOPES),
                "device_code": device_code,
                "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
            },
        )
        if st == 200 and isinstance(b, dict) and b.get("access_token"):
            await self._store_new(b)
            return "ok"
        msg = str((b or {}).get("message") or "") if isinstance(b, dict) else ""
        if msg == "authorization_pending":
            return "pending"
        if msg == "slow_down":
            return "slow_down"
        raise HttpError(st, b)

    async def _store_new(self, body):
        data = {"access_token": body["access_token"], "refresh_token": body.get("refresh_token", ""), "scopes": body.get("scope") or []}
        self.tokens.replace(data)
        v = await self.validate()
        if not v:
            self.clear()
            raise HttpError(401, {"message": "token did not validate"})

    async def validate(self):
        """Validates the access token and records user_id/login. Returns True, or False when it is invalid."""
        tok = self.tokens.data.get("access_token")
        if not tok:
            return False
        st, _h, b = await self._call("GET", ID_BASE + "/validate", headers={"Authorization": "OAuth " + tok})
        if st == 200 and isinstance(b, dict) and b.get("user_id"):
            d = dict(self.tokens.data)
            d.update({"user_id": str(b["user_id"]), "login": b.get("login", ""), "scopes": b.get("scopes") or d.get("scopes") or []})
            self.tokens.replace(d)
            return True
        if st == 401:
            return False
        raise HttpError(st, b)

    async def refresh(self):
        rt = self.tokens.data.get("refresh_token")
        if not rt:
            return False
        st, _h, b = await self._call("POST", ID_BASE + "/token", data={"client_id": self.client_id, "grant_type": "refresh_token", "refresh_token": rt})
        if st == 200 and isinstance(b, dict) and b.get("access_token"):
            d = dict(self.tokens.data)
            d.update({"access_token": b["access_token"], "refresh_token": b.get("refresh_token", rt)})
            self.tokens.replace(d)
            self.log("token refreshed")
            return True
        if st in (400, 401):
            return False
        raise HttpError(st, b)

    async def revoke(self):
        tok = self.tokens.data.get("access_token")
        if tok:
            try:
                await self._call("POST", ID_BASE + "/revoke", data={"client_id": self.client_id, "token": tok})
            except Exception:
                pass
        self.clear()

    # -- helix

    async def helix(self, path, params=None, _retry=True):
        tok = self.tokens.data.get("access_token")
        if not tok:
            raise HttpError(401, {"message": "not signed in"})
        url = HELIX + path
        if params:
            url += "?" + urllib.parse.urlencode(params, doseq=True)
        st, h, b = await self._call("GET", url, headers={"Client-Id": self.client_id, "Authorization": "Bearer " + tok})
        if st == 401 and _retry:
            if await self.refresh():
                return await self.helix(path, params, _retry=False)
            raise HttpError(401, {"message": "signed out"})
        if st != 200:
            raise HttpError(st, b, h)
        return b if isinstance(b, dict) else {}

    async def _paged(self, path, params, max_pages):
        out = []
        cursor = None
        for _ in range(max_pages):
            p = dict(params)
            if cursor:
                p["after"] = cursor
            b = await self.helix(path, p)
            out.extend(x for x in (b.get("data") or []) if isinstance(x, dict))
            cursor = (b.get("pagination") or {}).get("cursor")
            if not cursor:
                break
        return out

    async def followed_streams(self):
        return await self._paged("/streams/followed", {"user_id": self.user_id, "first": 100}, 5)

    async def followed_channels(self):
        return await self._paged("/channels/followed", {"user_id": self.user_id, "first": 100}, 50)

    async def users(self, ids):
        out = {}
        ids = [i for i in ids if str(i).isdigit()]
        for i in range(0, len(ids), 100):
            b = await self.helix("/users", [("id", x) for x in ids[i:i + 100]])
            for u in b.get("data") or []:
                out[str(u.get("id"))] = u
        return out


def stream_event(s, avatar=""):
    """The payload the frontend toasts and lists."""
    return {
        "stream_id": str(s.get("id") or ""),
        "user_id": str(s.get("user_id") or ""),
        "login": str(s.get("user_login") or ""),
        "name": str(s.get("user_name") or s.get("user_login") or ""),
        "game": str(s.get("game_name") or ""),
        "title": str(s.get("title") or ""),
        "viewers": int(s.get("viewer_count") or 0),
        "started_at": str(s.get("started_at") or ""),
        "avatar": avatar or "",
    }


class Engine:
    """One poll cycle at a time; main.py runs poll_once() in a loop and sleeps for the delay it returns.

    emit(event_dict) is an async callable used for go-live events that passed the filter.
    """

    def __init__(self, twitch, settings, bell, emit, log=None, now=time.time):
        self.twitch = twitch
        self.settings = settings  # JsonStore holding sanitized settings
        self.bell = bell  # BellExport
        self.emit = emit
        self.log = log or (lambda *_: None)
        self.now = now
        self.tracker = LiveTracker()
        self.backoff = Backoff()
        self.avatars = {}  # user_id -> url
        self.live = []  # stream_event dicts, most viewers first
        self.last_error = ""
        self.last_poll = None
        self.signed_out_reason = ""

    @property
    def cfg(self):
        return self.settings.data

    def reset(self):
        """After sign-in/out: start over (the next poll is a silent first poll again)."""
        self.tracker.reset()
        self.live = []
        self.last_error = ""

    async def _fill_avatars(self, ids):
        missing = [i for i in ids if i not in self.avatars]
        if not missing:
            return
        try:
            users = await self.twitch.users(missing)
        except (HttpError, NetworkError) as e:
            self.log("avatar lookup failed: %s" % e)
            return
        for uid, u in users.items():
            self.avatars[uid] = str(u.get("profile_image_url") or "")
        if len(self.avatars) > 3000:
            self.avatars.clear()

    async def poll_once(self):
        interval = self.cfg.get("interval", 60)
        if not self.twitch.signed_in:
            self.live = []
            return interval
        try:
            streams = await self.twitch.followed_streams()
        except NetworkError as e:
            self.last_error = "network: %s" % e
            delay = self.backoff.network(interval)
            self.log("poll failed (%s); next try in %ds" % (self.last_error, delay))
            return delay
        except HttpError as e:
            if e.status == 429:
                delay = self.backoff.rate_limited(interval, e.headers.get("ratelimit-reset"), self.now())
                self.last_error = "rate limited"
                self.log("rate limited; next try in %ds" % delay)
                return delay
            if e.status == 401:
                self.log("signed out by Twitch (refresh failed)")
                self.signed_out_reason = "Twitch signed this plugin out. Sign in again."
                self.twitch.clear()
                self.reset()
                return interval
            self.last_error = "http %s" % e.status
            delay = self.backoff.network(interval)
            self.log("poll failed (http %s); next try in %ds" % (e.status, delay))
            return delay
        self.last_error = ""
        self.last_poll = self.now()
        fresh = self.tracker.update(streams, self.now())
        await self._fill_avatars([str(s.get("user_id")) for s in streams if s.get("user_id")])
        self.live = sorted((stream_event(s, self.avatars.get(str(s.get("user_id")), "")) for s in streams), key=lambda e: -e["viewers"])
        if fresh and self.cfg.get("enabled", True):
            bell = self.bell.for_user(self.twitch.user_id) if self.cfg.get("source") == "bell" else None
            for s in fresh:
                uid = str(s.get("user_id"))
                if should_notify(uid, self.cfg, bell):
                    ev = stream_event(s, self.avatars.get(uid, ""))
                    self.log("live: %s" % ev["login"])
                    await self.emit(ev)
        return self.backoff.ok(interval)

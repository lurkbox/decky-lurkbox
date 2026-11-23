"""Lurkbox Decky companion: go-live notifications for followed Twitch channels.

The logic lives in py_modules/lurkbox_core.py; this file wires it to Decky (settings dir, logger, events) and exposes
the calls the QAM panel uses. Every entry point catches its own errors: the plugin must never take Decky down.
"""

import asyncio
import os
import sys
import time

import decky  # provided by Decky Loader

_HERE = os.path.dirname(os.path.abspath(__file__))
for _p in (os.path.join(_HERE, "py_modules"), _HERE):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import lurkbox_core as core  # noqa: E402

log = decky.logger


def _owner():
    """(uid, gid) of the Steam user when the backend runs as root, so files in Lurkbox's profile stay theirs."""
    if os.geteuid() != 0:
        return None
    try:
        import pwd

        pw = pwd.getpwnam(decky.DECKY_USER)
        return (pw.pw_uid, pw.pw_gid)
    except Exception:
        return None


def _safe(fn):
    """Decorator for frontend calls: log and return {"ok": False} instead of raising."""

    async def wrapper(self, *args):
        try:
            return await fn(self, *args)
        except Exception as e:  # noqa: BLE001
            log.exception("call %s failed", fn.__name__)
            return {"ok": False, "error": str(e)[:200]}

    wrapper.__name__ = fn.__name__
    return wrapper


async def _to_thread(fn, *a, **k):
    return await asyncio.to_thread(fn, *a, **k)


class Plugin:
    # ------------------------------------------------------------ lifecycle

    async def _main(self):
        try:
            self.settings_dir = decky.DECKY_PLUGIN_SETTINGS_DIR
            os.makedirs(self.settings_dir, exist_ok=True)
            # The AppImage's profile or the Flatpak's, whichever Lurkbox wrote the bell export to last.
            self.lurkbox_dirs = core.lurkbox_dirs(decky.DECKY_USER_HOME)
            self.owner = _owner()
            st = core.JsonStore(os.path.join(self.settings_dir, "settings.json"), core.DEFAULT_SETTINGS)
            st.data = core.sanitize_settings(st.data)
            self.settings = st
            self.tokens = core.JsonStore(os.path.join(self.settings_dir, "tokens.json"), {})
            if os.path.exists(self.tokens.path):
                os.chmod(self.tokens.path, 0o600)
            self.twitch = core.Twitch(self.tokens, run=_to_thread, log=lambda m: log.info("[twitch] %s", m))
            self.bell = core.BellExport(lambda: os.path.join(core.pick_lurkbox_dir(self.lurkbox_dirs), "notify-channels.json"))
            self.engine = core.Engine(self.twitch, self.settings, self.bell, self._emit_live, log=lambda m: log.info("[poll] %s", m))
            self.device = None
            self.device_error = ""
            self.sign_in_task = None
            self.follows = None
            self.follows_at = 0
            self.last_validate = 0
            self.wake = asyncio.Event()
            self.loop_task = asyncio.get_running_loop().create_task(self._loop())
            log.info("Lurkbox companion started (signed in: %s)", self.twitch.signed_in)
        except Exception:
            log.exception("startup failed")

    async def _unload(self):
        for t in (getattr(self, "loop_task", None), getattr(self, "sign_in_task", None)):
            if t:
                t.cancel()
        log.info("Lurkbox companion stopped")

    async def _uninstall(self):
        pass

    # ------------------------------------------------------------ background

    async def _loop(self):
        while True:
            delay = 60
            try:
                await self._maybe_validate()
                delay = await self.engine.poll_once()
                await self._emit_state()
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("poll loop error")
                delay = 120
            self.wake.clear()
            try:
                await asyncio.wait_for(self.wake.wait(), timeout=max(5, delay))
            except asyncio.TimeoutError:
                pass

    async def _maybe_validate(self):
        """Twitch asks apps to validate tokens hourly; a dead token gets one refresh before we sign out."""
        if not self.twitch.signed_in or time.time() - self.last_validate < 3600:
            return
        try:
            ok = await self.twitch.validate()
            if not ok and not (await self.twitch.refresh() and await self.twitch.validate()):
                log.info("token no longer valid; signed out")
                self.engine.signed_out_reason = "Twitch signed this plugin out. Sign in again."
                self.twitch.clear()
                self.engine.reset()
            self.last_validate = time.time()
        except core.NetworkError:
            pass  # try again next cycle
        except core.HttpError as e:
            log.info("validate: http %s", e.status)
            self.last_validate = time.time()

    async def _emit_live(self, ev):
        await decky.emit("lurkbox_live", ev)

    async def _emit_state(self):
        try:
            await decky.emit("lurkbox_state")
        except Exception:
            pass

    def _poke(self):
        self.wake.set()

    # ------------------------------------------------------------ calls from the QAM panel

    @_safe
    async def get_state(self):
        return {
            "ok": True,
            "signed_in": self.twitch.signed_in,
            "login": self.twitch.login,
            "user_id": self.twitch.user_id,
            "signed_out_reason": self.engine.signed_out_reason,
            "device": self._device_public(),
            "device_error": self.device_error,
            "settings": self.settings.data,
            "live": self.engine.live,
            "last_error": self.engine.last_error,
            "last_poll": self.engine.last_poll,
            "bell": self.bell.summary(self.twitch.user_id),
        }

    def _device_public(self):
        d = self.device
        if not d:
            return None
        return {"user_code": d["user_code"], "verification_uri": d["verification_uri"], "expires_at": d["expires_at"]}

    @_safe
    async def start_sign_in(self):
        if self.sign_in_task and not self.sign_in_task.done():
            self.sign_in_task.cancel()
        self.device_error = ""
        self.device = await self.twitch.start_device()
        self.sign_in_task = asyncio.get_running_loop().create_task(self._poll_device(self.device))
        log.info("device sign-in started")
        return {"ok": True, "device": self._device_public()}

    async def _poll_device(self, flow):
        interval = flow["interval"]
        try:
            while time.time() < flow["expires_at"]:
                await asyncio.sleep(interval)
                try:
                    r = await self.twitch.poll_device(flow["device_code"])
                except core.NetworkError:
                    continue
                if r == "slow_down":
                    interval += 5
                elif r == "ok":
                    log.info("signed in as %s", self.twitch.login)
                    self.device = None
                    self.engine.signed_out_reason = ""
                    self.engine.reset()
                    self.follows = None
                    self.last_validate = time.time()
                    self._poke()
                    await self._emit_state()
                    return
            self.device_error = "The code expired. Try again."
        except asyncio.CancelledError:
            raise
        except core.HttpError as e:
            self.device_error = "Sign-in failed (%s)." % (e.message or e.status)
            log.info("device sign-in failed: http %s %s", e.status, e.message)
        except Exception:
            log.exception("device sign-in error")
            self.device_error = "Sign-in failed."
        self.device = None
        await self._emit_state()

    @_safe
    async def cancel_sign_in(self):
        if self.sign_in_task:
            self.sign_in_task.cancel()
        self.device = None
        self.device_error = ""
        return {"ok": True}

    @_safe
    async def sign_out(self):
        await self.twitch.revoke()
        self.engine.reset()
        self.engine.signed_out_reason = ""
        self.follows = None
        log.info("signed out")
        return {"ok": True}

    @_safe
    async def set_settings(self, patch):
        merged = dict(self.settings.data)
        if isinstance(patch, dict):
            for k in ("enabled", "interval", "source", "quiet", "sound"):
                if k in patch:
                    merged[k] = patch[k]
        self.settings.replace(core.sanitize_settings(merged))
        if isinstance(patch, dict) and "interval" in patch:
            self._poke()
        return {"ok": True, "settings": self.settings.data}

    @_safe
    async def get_follows(self, force=False):
        if not self.twitch.signed_in:
            return {"ok": False, "error": "not signed in"}
        if force or self.follows is None or time.time() - self.follows_at > 600:
            rows = await self.twitch.followed_channels()
            self.follows = [
                {"id": str(r.get("broadcaster_id")), "login": str(r.get("broadcaster_login") or ""), "name": str(r.get("broadcaster_name") or "")}
                for r in rows
                if str(r.get("broadcaster_id") or "").isdigit()
            ]
            self.follows.sort(key=lambda r: r["name"].lower())
            self.follows_at = time.time()
        return {"ok": True, "channels": self._follow_rows()}

    def _follow_rows(self):
        cfg = self.settings.data
        bell = self.bell.for_user(self.twitch.user_id)
        src = cfg.get("source", "bell")
        out = []
        for f in self.follows or []:
            ov = cfg["overrides"].get(f["id"])
            base = core.base_notify(f["id"], src, bell if src == "bell" else None)
            out.append({
                **f,
                "bell": None if bell is None else bell.get(f["id"], True),
                "base": base,
                "mode": ov["mode"] if ov else "",
                "notify": core.should_notify(f["id"], cfg, bell if src == "bell" else None),
            })
        return out

    @_safe
    async def set_channel(self, user_id, login, want):
        """Toggle in the per-channel list: matching the source's own answer clears the override."""
        user_id = str(user_id)
        if not user_id.isdigit():
            return {"ok": False, "error": "bad id"}
        cfg = dict(self.settings.data)
        ov = dict(cfg["overrides"])
        src = cfg.get("source", "bell")
        bell = self.bell.for_user(self.twitch.user_id) if src == "bell" else None
        if bool(want) == core.base_notify(user_id, src, bell):
            ov.pop(user_id, None)
        else:
            ov[user_id] = {"login": str(login or "").lower(), "mode": "force" if want else "mute"}
        cfg["overrides"] = ov
        self.settings.replace(core.sanitize_settings(cfg))
        return {"ok": True, "channels": self._follow_rows()}

    @_safe
    async def clear_overrides(self):
        cfg = dict(self.settings.data)
        cfg["overrides"] = {}
        self.settings.replace(core.sanitize_settings(cfg))
        return {"ok": True}

    @_safe
    async def open_channel(self, login, name=""):
        path = core.write_open_request(core.pick_lurkbox_dir(self.lurkbox_dirs), str(login or ""), name, owner=self.owner)
        log.info("open request for %s written", login)
        return {"ok": True, "path": path}

    @_safe
    async def refresh_now(self):
        self._poke()
        return {"ok": True}

    @_safe
    async def test_notification(self):
        """Sends a notification through the same event path as a real go-live."""
        if self.engine.live:
            ev = dict(self.engine.live[0])
        else:
            login = self.twitch.login or "lurkbox"
            ev = core.stream_event({"id": "test", "user_id": self.twitch.user_id or "0", "user_login": login, "user_name": login, "game_name": "Just Chatting", "title": "This is a test notification"})
        ev["test"] = True
        await self._emit_live(ev)
        return {"ok": True}

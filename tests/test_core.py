"""Unit tests for py_modules/lurkbox_core.py (stdlib unittest; run with `python3 -m unittest discover -s tests`)."""

import asyncio
import json
import os
import stat
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "py_modules"))

import lurkbox_core as core  # noqa: E402


def stream(uid, sid, login=None, viewers=10, game="Game", title="Title"):
    login = login or "user%s" % uid
    return {"id": sid, "user_id": uid, "user_login": login, "user_name": login.title(), "game_name": game, "title": title, "viewer_count": viewers}


def run(coro):
    return asyncio.run(coro)


class TrackerTests(unittest.TestCase):
    def test_first_poll_is_silent(self):
        t = core.LiveTracker()
        self.assertEqual(t.update([stream("1", "a"), stream("2", "b")]), [])
        self.assertTrue(t.primed)

    def test_new_channel_after_first_poll(self):
        t = core.LiveTracker()
        t.update([stream("1", "a")])
        fresh = t.update([stream("1", "a"), stream("2", "b")])
        self.assertEqual([s["user_id"] for s in fresh], ["2"])
        self.assertEqual(t.update([stream("1", "a"), stream("2", "b")]), [])

    def test_empty_first_poll_still_primes(self):
        t = core.LiveTracker()
        self.assertEqual(t.update([]), [])
        self.assertEqual(len(t.update([stream("1", "a")])), 1)

    def test_same_stream_id_never_notifies_twice(self):
        t = core.LiveTracker()
        t.update([])
        self.assertEqual(len(t.update([stream("1", "a")])), 1)
        self.assertEqual(t.update([]), [])  # dropped out of the list (API hiccup or short offline)
        self.assertEqual(t.update([stream("1", "a")]), [])  # same stream back: quiet

    def test_new_stream_after_offline_notifies(self):
        t = core.LiveTracker()
        t.update([])
        t.update([stream("1", "a")])
        t.update([])
        self.assertEqual(len(t.update([stream("1", "b")])), 1)

    def test_channel_live_at_boot_is_remembered(self):
        t = core.LiveTracker()
        t.update([stream("1", "a")])
        t.update([])
        self.assertEqual(t.update([stream("1", "a")]), [])

    def test_seen_is_bounded(self):
        t = core.LiveTracker()
        t.MAX_SEEN = 5
        for i in range(20):
            t.update([stream(str(i), "s%d" % i)])
        self.assertLessEqual(len(t.seen), 5)

    def test_reset_makes_next_poll_silent(self):
        t = core.LiveTracker()
        t.update([])
        t.reset()
        self.assertEqual(t.update([stream("1", "a")]), [])


class BackoffTests(unittest.TestCase):
    def test_network_doubles_and_caps(self):
        b = core.Backoff()
        self.assertEqual(b.network(60), 120)
        self.assertEqual(b.network(60), 240)
        self.assertEqual(b.network(60), 480)
        self.assertEqual(b.network(60), 900)
        self.assertEqual(b.network(60), 900)
        self.assertEqual(b.ok(60), 60)
        self.assertEqual(b.network(60), 120)

    def test_rate_limit_waits_for_reset(self):
        b = core.Backoff()
        self.assertEqual(b.rate_limited(30, reset_at="1100", now=1000), 101)
        self.assertEqual(b.rate_limited(30, reset_at="1001", now=1000), 30)  # never faster than the interval
        self.assertEqual(b.rate_limited(30, reset_at=None, now=1000), 61)
        self.assertEqual(b.rate_limited(30, reset_at="junk", now=1000), 61)
        self.assertEqual(b.rate_limited(30, reset_at="999999", now=1000), 900)


class FilterTests(unittest.TestCase):
    def cfg(self, source, overrides=None):
        return core.sanitize_settings({"source": source, "overrides": overrides or {}})

    def test_bell(self):
        bell = {"1": True, "2": False}
        c = self.cfg("bell")
        self.assertTrue(core.should_notify("1", c, bell))
        self.assertFalse(core.should_notify("2", c, bell))
        self.assertTrue(core.should_notify("3", c, bell))  # followed after the last export: Twitch's default (on)

    def test_bell_without_export_falls_back_to_all(self):
        self.assertTrue(core.should_notify("2", self.cfg("bell"), None))

    def test_all_and_list(self):
        self.assertTrue(core.should_notify("2", self.cfg("all"), {"2": False}))
        self.assertFalse(core.should_notify("1", self.cfg("list"), {"1": True}))

    def test_mute_and_force(self):
        ov = {"1": {"login": "a", "mode": "mute"}, "2": {"login": "b", "mode": "force"}}
        for src in ("bell", "all", "list"):
            c = self.cfg(src, ov)
            self.assertFalse(core.should_notify("1", c, {"1": True}), src)
            self.assertTrue(core.should_notify("2", c, {"2": False}), src)

    def test_sanitize(self):
        s = core.sanitize_settings({"interval": 45, "source": "x", "quiet": "quiet", "enabled": "yes",
                                    "overrides": {"1": {"mode": "mute", "login": "Ok_1"}, "x": {"mode": "mute"}, "2": {"mode": "maybe"}, "3": {"mode": "force", "login": "../bad"}}})
        self.assertEqual(s["interval"], 60)
        self.assertEqual(s["source"], "bell")
        self.assertEqual(s["quiet"], "quiet")
        self.assertIs(s["enabled"], True)
        self.assertEqual(s["overrides"], {"1": {"login": "ok_1", "mode": "mute"}, "3": {"login": "", "mode": "force"}})
        self.assertEqual(core.sanitize_settings({"interval": "120"})["interval"], 120)
        self.assertEqual(core.sanitize_settings(None), core.DEFAULT_SETTINGS)


class BellExportTests(unittest.TestCase):
    def test_reads_matching_user_only(self):
        d = tempfile.mkdtemp()
        p = os.path.join(d, "notify-channels.json")
        b = core.BellExport(p)
        self.assertIsNone(b.for_user("42"))
        self.assertFalse(b.summary("42")["available"])
        with open(p, "w") as f:
            json.dump({"updated_at": "x", "user_id": "42", "channels": [{"id": "1", "login": "a", "notify": True}, {"id": "2", "login": "b", "notify": False}, {"id": "zz"}]}, f)
        self.assertEqual(b.for_user("42"), {"1": True, "2": False})
        self.assertIsNone(b.for_user("43"))
        s = b.summary("42")
        self.assertEqual((s["available"], s["matches"], s["count"], s["on"]), (True, True, 3, 1))

    def test_malformed_file(self):
        d = tempfile.mkdtemp()
        p = os.path.join(d, "notify-channels.json")
        with open(p, "w") as f:
            f.write("{not json")
        self.assertIsNone(core.BellExport(p).for_user("42"))


class OpenRequestTests(unittest.TestCase):
    def test_writes_fresh_request_0600(self):
        d = os.path.join(tempfile.mkdtemp(), "Lurkbox")
        p = core.write_open_request(d, "SomeOne", "SomeOne", now=1700000000.5)
        with open(p) as f:
            data = json.load(f)
        self.assertEqual(data, {"channel": "someone", "at": 1700000000500, "name": "SomeOne"})
        self.assertEqual(stat.S_IMODE(os.stat(p).st_mode), 0o600)
        self.assertEqual(sorted(os.listdir(d)), ["open-request.json"])

    def test_overwrites_and_rejects_bad_logins(self):
        d = tempfile.mkdtemp()
        core.write_open_request(d, "first")
        core.write_open_request(d, "second", "x" * 80)
        with open(os.path.join(d, "open-request.json")) as f:
            data = json.load(f)
        self.assertEqual(data["channel"], "second")
        self.assertNotIn("name", data)
        for bad in ("", "../etc", "a" * 26, None, "has space"):
            with self.assertRaises(ValueError):
                core.write_open_request(d, bad)


class FakeHttp:
    """Routes (method, url-substring) to (status, headers, body) responses; records calls."""

    def __init__(self, routes):
        self.routes = routes
        self.calls = []

    def __call__(self, method, url, headers=None, data=None, timeout=20):
        self.calls.append((method, url, headers or {}, data))
        for (m, frag), resp in self.routes:
            if m == method and frag in url:
                r = resp(url, data) if callable(resp) else resp
                if isinstance(r, Exception):
                    raise r
                return r
        raise AssertionError("unexpected %s %s" % (method, url))


def mem_store(data):
    s = core.JsonStore(os.path.join(tempfile.mkdtemp(), "x.json"), {})
    s.data = data
    return s


class EngineTests(unittest.TestCase):
    def make(self, streams_resp, settings=None, bell_data=None):
        tokens = mem_store({"access_token": "AT", "refresh_token": "RT", "user_id": "42", "login": "me"})
        http = FakeHttp([
            (("GET", "/helix/streams/followed"), streams_resp),
            (("GET", "/helix/users"), (200, {}, {"data": [{"id": "1", "profile_image_url": "https://img/1.png"}, {"id": "2", "profile_image_url": "https://img/2.png"}]})),
            (("POST", "/oauth2/token"), (400, {}, {"message": "Invalid refresh token"})),
        ])
        tw = core.Twitch(tokens, http=http)
        d = tempfile.mkdtemp()
        bp = os.path.join(d, "notify-channels.json")
        if bell_data is not None:
            with open(bp, "w") as f:
                json.dump(bell_data, f)
        events = []

        async def emit(ev):
            events.append(ev)

        eng = core.Engine(tw, mem_store(core.sanitize_settings(settings or {})), core.BellExport(bp), emit, now=lambda: 1000.0)
        return eng, events, http, tokens

    def test_poll_cycle_notifies_new_channels_through_the_filter(self):
        polls = [[stream("1", "a")], [stream("1", "a"), stream("2", "b", viewers=99)], [stream("1", "a"), stream("2", "b"), stream("3", "c")]]
        state = {"i": 0}

        def resp(_u, _d):
            r = (200, {}, {"data": polls[min(state["i"], len(polls) - 1)], "pagination": {}})
            state["i"] += 1
            return r

        bell = {"user_id": "42", "channels": [{"id": "1", "notify": True}, {"id": "2", "notify": True}, {"id": "3", "notify": False}]}
        eng, events, http, _ = self.make(resp, {"source": "bell"}, bell)
        self.assertEqual(run(eng.poll_once()), 60)
        self.assertEqual(events, [])  # first poll
        run(eng.poll_once())
        self.assertEqual([e["login"] for e in events], ["user2"])
        self.assertEqual(events[0]["avatar"], "https://img/2.png")
        self.assertEqual(events[0]["game"], "Game")
        self.assertEqual(eng.live[0]["user_id"], "2")  # most viewers first
        run(eng.poll_once())
        self.assertEqual(len(events), 1)  # user3 has the bell off
        hdrs = http.calls[0][2]
        self.assertEqual(hdrs["Client-Id"], core.CLIENT_ID)
        self.assertEqual(hdrs["Authorization"], "Bearer AT")
        self.assertIn("user_id=42", http.calls[0][1])

    def test_master_switch_off_suppresses(self):
        polls = [[], [stream("1", "a")]]
        it = iter(polls)
        eng, events, _, _ = self.make(lambda u, d: (200, {}, {"data": next(it)}), {"enabled": False})
        run(eng.poll_once())
        run(eng.poll_once())
        self.assertEqual(events, [])
        self.assertEqual(len(eng.live), 1)

    def test_rate_limit_and_network_backoff(self):
        eng, _, _, _ = self.make((429, {"ratelimit-reset": "1200"}, {"message": "Too Many Requests"}))
        self.assertEqual(run(eng.poll_once()), 201)
        self.assertEqual(eng.last_error, "rate limited")
        eng2, _, _, _ = self.make(lambda u, d: core.NetworkError("down"))
        self.assertEqual(run(eng2.poll_once()), 120)
        self.assertEqual(run(eng2.poll_once()), 240)
        eng3, _, _, _ = self.make((503, {}, {}))
        self.assertEqual(run(eng3.poll_once()), 120)

    def test_401_with_failed_refresh_signs_out(self):
        eng, _, http, tokens = self.make((401, {}, {"message": "Invalid OAuth token"}))
        run(eng.poll_once())
        self.assertFalse(eng.twitch.signed_in)
        self.assertEqual(tokens.data, {})
        self.assertTrue(eng.signed_out_reason)
        self.assertEqual(http.calls[1][3]["grant_type"], "refresh_token")

    def test_not_signed_in_does_nothing(self):
        eng, _, http, tokens = self.make((200, {}, {"data": []}))
        tokens.data = {}
        self.assertEqual(run(eng.poll_once()), 60)
        self.assertEqual(http.calls, [])


class TwitchAuthTests(unittest.TestCase):
    def test_device_flow_and_token_file_mode(self):
        d = tempfile.mkdtemp()
        tokens = core.JsonStore(os.path.join(d, "tokens.json"), {})
        answers = iter([(400, {}, {"status": 400, "message": "authorization_pending"}), (200, {}, {"access_token": "AT", "refresh_token": "RT", "scope": ["user:read:follows"]})])
        http = FakeHttp([
            (("POST", "/oauth2/device"), (200, {}, {"device_code": "DC", "user_code": "ABCD1234", "verification_uri": "https://www.twitch.tv/activate?public=true&device-code=ABCD1234", "expires_in": 1800, "interval": 5})),
            (("POST", "/oauth2/token"), lambda u, data: next(answers)),
            (("GET", "/oauth2/validate"), (200, {}, {"user_id": "42", "login": "me", "scopes": ["user:read:follows"]})),
        ])
        tw = core.Twitch(tokens, http=http)
        flow = run(tw.start_device())
        self.assertEqual(flow["user_code"], "ABCD1234")
        self.assertEqual(http.calls[0][3], {"client_id": core.CLIENT_ID, "scopes": "user:read:follows"})
        self.assertEqual(run(tw.poll_device("DC")), "pending")
        self.assertEqual(run(tw.poll_device("DC")), "ok")
        self.assertTrue(tw.signed_in)
        self.assertEqual((tw.user_id, tw.login), ("42", "me"))
        self.assertEqual(stat.S_IMODE(os.stat(tokens.path).st_mode), 0o600)
        with open(tokens.path) as f:
            self.assertEqual(json.load(f)["refresh_token"], "RT")

    def test_device_flow_denied_raises(self):
        tokens = mem_store({})
        http = FakeHttp([(("POST", "/oauth2/token"), (400, {}, {"message": "authorization_denied"}))])
        with self.assertRaises(core.HttpError):
            run(core.Twitch(tokens, http=http).poll_device("DC"))

    def test_helix_refreshes_once_on_401(self):
        tokens = mem_store({"access_token": "OLD", "refresh_token": "RT", "user_id": "42"})
        seen = []

        def streams(url, _d):
            tok = http.calls[-1][2]["Authorization"]
            seen.append(tok)
            return (401, {}, {}) if tok == "Bearer OLD" else (200, {}, {"data": [stream("1", "a")]})

        http = FakeHttp([
            (("GET", "/helix/streams/followed"), streams),
            (("POST", "/oauth2/token"), (200, {}, {"access_token": "NEW", "refresh_token": "RT2"})),
        ])
        tw = core.Twitch(tokens, http=http)
        self.assertEqual(len(run(tw.followed_streams())), 1)
        self.assertEqual(seen, ["Bearer OLD", "Bearer NEW"])
        self.assertEqual(tokens.data["refresh_token"], "RT2")

    def test_pagination(self):
        tokens = mem_store({"access_token": "AT", "user_id": "42"})
        pages = iter([(200, {}, {"data": [{"broadcaster_id": "1"}], "pagination": {"cursor": "c1"}}), (200, {}, {"data": [{"broadcaster_id": "2"}], "pagination": {}})])
        http = FakeHttp([(("GET", "/helix/channels/followed"), lambda u, d: next(pages))])
        rows = run(core.Twitch(tokens, http=http).followed_channels())
        self.assertEqual([r["broadcaster_id"] for r in rows], ["1", "2"])
        self.assertIn("after=c1", http.calls[1][1])


if __name__ == "__main__":
    unittest.main()


class SoundSettingTest(unittest.TestCase):
    def test_sound_defaults_on_and_accepts_bools_only(self):
        self.assertTrue(core.sanitize_settings({})["sound"])
        self.assertFalse(core.sanitize_settings({"sound": False})["sound"])
        self.assertTrue(core.sanitize_settings({"sound": "no"})["sound"])

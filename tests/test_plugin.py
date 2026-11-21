"""Loads main.py with a stand-in `decky` module and drives the QAM calls (no network: signed out)."""

import asyncio
import importlib.util
import json
import logging
import os
import sys
import tempfile
import types
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.join(HERE, "..")


def load_plugin(home):
    events = []
    fake = types.ModuleType("decky")
    fake.logger = logging.getLogger("lurkbox-test")
    fake.logger.setLevel(logging.CRITICAL + 1)
    fake.DECKY_USER = os.environ.get("USER", "deck")
    fake.DECKY_USER_HOME = home
    fake.DECKY_PLUGIN_SETTINGS_DIR = os.path.join(home, "homebrew", "settings", "Lurkbox")

    async def emit(event, *args):
        events.append((event, args))

    fake.emit = emit
    sys.modules["decky"] = fake
    spec = importlib.util.spec_from_file_location("lurkbox_main", os.path.join(ROOT, "main.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.Plugin(), events, fake


class PluginTests(unittest.TestCase):
    def test_calls_signed_out(self):
        home = tempfile.mkdtemp()
        plugin, events, fake = load_plugin(home)

        async def go():
            await plugin._main()
            st = await plugin.get_state()
            self.assertTrue(st["ok"])
            self.assertFalse(st["signed_in"])
            self.assertEqual(st["settings"]["source"], "bell")
            self.assertFalse(st["bell"]["available"])

            r = await plugin.set_settings({"interval": 30, "quiet": "quiet", "bogus": 1})
            self.assertEqual((r["settings"]["interval"], r["settings"]["quiet"]), (30, "quiet"))
            with open(os.path.join(fake.DECKY_PLUGIN_SETTINGS_DIR, "settings.json")) as f:
                self.assertEqual(json.load(f)["interval"], 30)

            r = await plugin.open_channel("SomeChannel", "SomeChannel")
            self.assertTrue(r["ok"])
            with open(os.path.join(home, ".config", "Lurkbox", "open-request.json")) as f:
                self.assertEqual(json.load(f)["channel"], "somechannel")

            bad = await plugin.open_channel("../../etc/passwd")
            self.assertFalse(bad["ok"])  # errors come back as values, never raise into Decky

            self.assertFalse((await plugin.get_follows())["ok"])
            self.assertTrue((await plugin.test_notification())["ok"])
            self.assertEqual(events[-1][0], "lurkbox_live")
            self.assertTrue(events[-1][1][0]["test"])
            await plugin._unload()

        asyncio.run(go())


if __name__ == "__main__":
    unittest.main()

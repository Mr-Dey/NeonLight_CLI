"""Headless tests of the terminal UI with fake strips (no Bluetooth needed).

Run:  .venv/bin/python -m unittest discover -s tests -v
Set LIGHTUI_SCREENSHOT=/some/dir to also save SVG screenshots of the tabs.
"""
import asyncio
import os
import tempfile
import unittest

import lightctl as L
import lightui as U


class FakeClient:
    is_connected = True

    def __init__(self):
        self.writes = []

    async def write_gatt_char(self, char, data, response):
        self.writes.append(bytes(data))

    async def disconnect(self):
        pass


class FakeGroup(L.Group):
    async def connect_missing(self):
        new = [d for d in self.devices if d not in self.clients]
        for d in new:
            self.clients[d] = FakeClient()
        return new

    async def __aexit__(self, *exc):
        pass


DEVICES = [L.Device("monitor", "AA:BB:CC:DD:EE:01", "gbr"), L.Device("room", "AA:BB:CC:DD:EE:02", "rgb")]


class UITest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.saved = (U.SETTINGS_FILE, L.Group, L.music_effects, L.music, L.screen_sync, L.animate, L.tour)
        U.SETTINGS_FILE = os.path.join(self.tmp.name, "ui.json")
        L.Group = FakeGroup
        self.started: list[str] = []

        def fake(name):
            async def run(*args, **kw):
                self.started.append(name)
                await asyncio.Event().wait()  # runs until the UI stops it
            return run

        L.music_effects, L.music = fake("music_effects"), fake("music")
        L.screen_sync, L.animate, L.tour = fake("screen"), fake("animate"), fake("tour")

    async def asyncTearDown(self):
        (U.SETTINGS_FILE, L.Group, L.music_effects, L.music, L.screen_sync, L.animate, L.tour) = self.saved
        L.log = lambda msg, err=True: None
        self.tmp.cleanup()

    def writes(self, app):
        return {d.name: c.writes for d, c in app.group.clients.items()}

    async def test_everything(self):
        app = U.LightUI(list(DEVICES))
        shots = os.environ.get("LIGHTUI_SCREENSHOT")
        async with app.run_test(size=(110, 42)) as pilot:
            await pilot.pause(0.3)
            self.assertEqual(len(app.group.clients), 2)  # both strips connected
            if shots:
                app.save_screenshot("tab1-light.svg", shots)

            # colour button -> both strips, each in its own wiring
            await pilot.click("#col-red")
            await pilot.pause(0.2)
            w = self.writes(app)
            self.assertIn(L.cmd_color(255, 0, 0, order="gbr"), w["monitor"])
            self.assertIn(L.cmd_color(255, 0, 0, order="rgb"), w["room"])

            # brightness slider: right arrow -> one coalesced brightness frame
            app.query_one("#brightness").focus()
            await pilot.press("left", "left")
            await pilot.pause(0.3)
            self.assertIn(L.cmd_brightness(90, 0x01), self.writes(app)["room"])
            self.assertEqual(app.s["brightness"], 90)

            # effects tab: search + pick
            await pilot.press("2")
            await pilot.pause(0.1)
            app.query_one("#effect-search").value = "7-color-jump"
            await pilot.pause(0.2)
            ol = app.query_one("#effect-list")
            self.assertEqual(ol.option_count, 1)
            ol.focus()
            ol.highlighted = 0
            await pilot.press("enter")
            await pilot.pause(0.2)
            self.assertIn(L.cmd_mode(0xC1), self.writes(app)["monitor"])
            if shots:
                app.save_screenshot("tab2-effects.svg", shots)

            # music on, then screen on -> music switches off; x stops everything
            await pilot.press("3")
            await pilot.pause(0.1)
            if shots:
                app.save_screenshot("tab3-music.svg", shots)
            await pilot.click("#music-on")
            await pilot.pause(0.2)
            self.assertEqual(app.mode_name, "music")
            self.assertEqual(self.started[-1], "music_effects")  # default style: animated
            await pilot.press("4")
            await pilot.pause(0.1)
            await pilot.click("#screen-on")
            await pilot.pause(0.2)
            self.assertEqual(app.mode_name, "screen")
            self.assertFalse(app.query_one("#music-on").value)
            if shots:
                app.save_screenshot("tab4-screen.svg", shots)
            await pilot.press("x")
            await pilot.pause(0.2)
            self.assertIsNone(app.mode_name)
            self.assertFalse(app.query_one("#screen-on").value)

            # strips tab: leave 'room' out -> it gets nothing
            await pilot.press("7")
            await pilot.pause(0.1)
            await pilot.click("#strip-1")
            await pilot.pause(0.1)
            before = len(self.writes(app)["room"])
            await pilot.press("1")
            await pilot.pause(0.1)
            await pilot.click("#col-blue")
            await pilot.pause(0.2)
            self.assertEqual(len(self.writes(app)["room"]), before)
            self.assertIn(L.cmd_color(0, 0, 255, order="gbr"), self.writes(app)["monitor"])
            if shots:
                await pilot.press("7")
                await pilot.pause(0.1)
                app.save_screenshot("tab7-strips.svg", shots)
            await pilot.press("q")
            await pilot.pause(0.2)

        # settings survive a restart
        s = U.load_settings()
        self.assertEqual(s["brightness"], 90)
        self.assertEqual(s["muted"], ["room"])
        self.assertEqual(s["color"], "blue")


if __name__ == "__main__":
    unittest.main()

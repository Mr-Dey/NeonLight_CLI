"""The Windows back-ends (winbackend.py) and the Windows UI, run on any OS with stand-ins for the
Windows-only libraries (soundcard, mss). Needs numpy and Pillow (installed on Windows anyway).

Run:  .venv/bin/python -m unittest discover -s tests -v
"""
import asyncio
import builtins
import math
import os
import sys
import tempfile
import threading
import time
import types
import unittest

try:
    import numpy as np
    from PIL import Image  # noqa: F401
    HAVE_DEPS = True
except ImportError:
    HAVE_DEPS = False

import lightctl as L
import winbackend as W

SPK, MIC = "{0.0.0.00000000}.{speakers}", "{0.0.1.00000000}.{webcam}"


class FakeRecorder:
    def __init__(self, amplitude):
        self.amplitude = amplitude

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def record(self, numframes):
        time.sleep(0.005)
        return np.full((numframes, 1), self.amplitude, dtype=np.float32)


class FakeMic:
    def __init__(self, id_, name, isloopback=False, amplitude=0.1):
        self.id, self.name, self.isloopback, self.amplitude = id_, name, isloopback, amplitude

    def recorder(self, samplerate, channels=None, blocksize=None):
        return FakeRecorder(self.amplitude)


def fake_soundcard():
    devices = [FakeMic(SPK, "Speakers (Realtek(R) Audio)", True, 0.1),
               FakeMic(MIC, "Microphone (HD Pro Webcam C920)", False, 0.01)]
    return types.SimpleNamespace(
        all_microphones=lambda include_loopback=False: [d for d in devices if include_loopback or not d.isloopback],
        default_speaker=lambda: types.SimpleNamespace(id=SPK, name="Speakers (Realtek(R) Audio)"),
        default_microphone=lambda: types.SimpleNamespace(id=MIC, name="Microphone (HD Pro Webcam C920)"),
        get_microphone=lambda id_, include_loopback=False: next(d for d in devices if d.id == id_),
    )


class FakeShot:
    def __init__(self, w, h, rgb):
        self.size = (w, h)
        self.bgra = bytes([rgb[2], rgb[1], rgb[0], 255]) * (w * h)


class FakeSct:
    # mss numbering: 0 = all monitors, then each monitor
    monitors = [{"left": 0, "top": 0, "width": 160, "height": 54},
                {"left": 0, "top": 0, "width": 96, "height": 54, "is_primary": True, "name": "Dell"},
                {"left": 96, "top": 0, "width": 64, "height": 36, "is_primary": False}]

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def grab(self, region):
        if region["left"] >= 96:
            colour = (0, 0, 255)      # monitor 2: blue
        elif region["left"] == 10:
            colour = (0, 255, 0)      # the captured window: green
        else:
            colour = (255, 0, 0)      # primary monitor: red
        return FakeShot(region["width"], region["height"], colour)


@unittest.skipUnless(HAVE_DEPS, "needs numpy and Pillow")
class WindowsBackends(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.saved = (sys.modules.get("soundcard"), sys.modules.get("mss"), L.IS_WINDOWS,
                      W.list_windows, W.window_rect)
        sys.modules["soundcard"] = fake_soundcard()
        sys.modules["mss"] = types.SimpleNamespace(mss=lambda **kw: FakeSct())
        L.IS_WINDOWS = True

    def tearDown(self):
        sc, mss, L.IS_WINDOWS, W.list_windows, W.window_rect = self.saved
        for name, mod in (("soundcard", sc), ("mss", mss)):
            if mod is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = mod

    async def test_audio_sources_and_choice(self):
        srcs = L.audio_sources()
        self.assertEqual([(s["desc"], s["monitor"]) for s in srcs],
                         [("Speakers (Realtek(R) Audio)", True), ("Microphone (HD Pro Webcam C920)", False)])
        self.assertEqual(L.default_audio(), (SPK, MIC))
        self.assertEqual(L.pick_audio_source("system")["name"], SPK)
        self.assertEqual(L.pick_audio_source("mic")["name"], MIC)
        self.assertEqual(L.pick_audio_source("webcam")["name"], MIC)
        self.assertEqual(L.pick_audio_source("2")["name"], MIC)

    async def test_loopback_levels_on_the_apps_scale(self):
        async with L.AudioInput("system", 10) as audio:
            self.assertEqual(audio.kind, "music")
            levels = [await audio.level() for _ in range(3)]
        expected = 10 * math.log10(0.1 ** 2 * 32768 ** 2)  # constant 0.1 as if 16-bit: ~70.3 dB
        for v in levels:
            self.assertAlmostEqual(v, expected, places=3)

    async def test_mic_levels(self):
        async with L.AudioInput("webcam", 10) as audio:
            self.assertEqual(audio.kind, "mic")
            self.assertAlmostEqual(await audio.level(), 10 * math.log10(0.01 ** 2 * 32768 ** 2), places=3)

    async def _frame(self, target):
        cap = L.open_screen_capture(12, False, target)
        self.assertIsInstance(cap, W.WinScreenCapture)
        async with cap:
            return await asyncio.wait_for(cap.frame(), 5)

    async def test_primary_monitor(self):
        frame = await self._frame("monitor")
        self.assertEqual(len(frame), L.SCREEN_W * L.SCREEN_H * 3)
        self.assertEqual(frame[:3], bytes([255, 0, 0]))

    async def test_second_monitor(self):
        self.assertEqual((await self._frame("monitor:2"))[:3], bytes([0, 0, 255]))

    async def test_window_by_title(self):
        W.list_windows = lambda: [{"hwnd": 42, "title": "Big Buck Bunny - VLC media player", "rect": (10, 10, 74, 46)}]
        W.window_rect = lambda hwnd: (10, 10, 74, 46) if hwnd == 42 else None
        self.assertEqual((await self._frame("window:vlc"))[:3], bytes([0, 255, 0]))

    async def test_unknown_window(self):
        W.list_windows = lambda: []
        with self.assertRaises(SystemExit):
            await self._frame("window:nothing like this")

    def test_capture_targets(self):
        self.assertEqual(L.parse_capture("Window:VLC media player"), "window:VLC media player")
        self.assertEqual(L.parse_capture("monitor:2"), "monitor:2")
        for bad in ("monitor:two", "screen"):
            with self.assertRaises(Exception):
                L.parse_capture(bad)

    def test_soundcard_imported_on_its_own_thread(self):
        # soundcard's import calls CoInitializeEx and raises RuntimeError('Error 0x100000001') on a
        # thread where COM is already set up, so winbackend imports it on a thread of its own.
        saved_sc = sys.modules.pop("soundcard", None)
        saved_import, saved_com_init = builtins.__import__, W._com_init
        fake, import_threads, com_threads = fake_soundcard(), [], []

        def spy_import(name, *args, **kw):
            if name == "soundcard":
                import_threads.append(threading.get_ident())
                sys.modules["soundcard"] = fake
                return fake
            return saved_import(name, *args, **kw)

        def spy_com_init():
            com_threads.append(threading.get_ident())
            return saved_com_init()

        builtins.__import__, W._com_init = spy_import, spy_com_init
        try:
            srcs = L.audio_sources()
        finally:
            builtins.__import__, W._com_init = saved_import, saved_com_init
            sys.modules.pop("soundcard", None)
            if saved_sc is not None:
                sys.modules["soundcard"] = saved_sc
        self.assertEqual(len(srcs), 2)
        self.assertEqual(len(import_threads), 1)
        self.assertNotIn(import_threads[0], com_threads)  # not on the thread that set COM up


class FakeScanner:
    """A Bluetooth scan that hears the given (address, name) pairs as soon as it starts."""
    heard: list = []

    def __init__(self, detection_callback):
        self.callback = detection_callback

    async def __aenter__(self):
        for addr, name in self.heard:
            self.callback(types.SimpleNamespace(address=addr, name=name, details="scan record"), None)
        return self

    async def __aexit__(self, *exc):
        return False


class FakeBleakClient:
    """Connects to scanned strips; to one given only by address (no scan record) only if Windows knows it."""
    known: set = set()
    made: list = []

    def __init__(self, device, timeout):
        self.device, self.is_connected = device, False
        FakeBleakClient.made.append(device)

    async def connect(self):
        if self.device.details is None and self.device.address not in self.known:
            raise RuntimeError(f"Device with address {self.device.address} was not found.")
        self.is_connected = True

    async def disconnect(self):
        self.is_connected = False


class FindingStrips(unittest.IsolatedAsyncioTestCase):
    """Some USB Bluetooth dongles (Actions ATS2851) hear no LE advertisements at all on Windows,
    yet Windows can still connect to a strip it has seen before, straight from its address."""
    MON = L.Device("monitor", "BE:69:32:13:B6:00", "rgb")
    ROOM = L.Device("room", "BE:69:04:14:32:04", "rgb")

    def setUp(self):
        self.saved = (L.BleakScanner, L.BleakClient, L.IS_WINDOWS, L.log, L.Group.SCAN_TIMEOUT)
        L.BleakScanner, L.BleakClient, L.Group.SCAN_TIMEOUT = FakeScanner, FakeBleakClient, 0.2
        self.warnings: list[str] = []
        L.log = lambda msg, err=True: self.warnings.append(msg)
        FakeBleakClient.made = []

    def tearDown(self):
        L.BleakScanner, L.BleakClient, L.IS_WINDOWS, L.log, L.Group.SCAN_TIMEOUT = self.saved

    async def connect(self, windows: bool, heard: list, known: set) -> L.Group:
        L.IS_WINDOWS = windows
        FakeScanner.heard, FakeBleakClient.known = heard, known
        g = L.Group([self.MON, self.ROOM])
        await g._find_and_connect(g.devices)
        return g

    async def test_windows_reaches_a_known_strip_when_the_scan_hears_nothing(self):
        g = await self.connect(True, heard=[], known={self.ROOM.addr})
        self.assertEqual(g.live, [self.ROOM])
        self.assertIsNone(g.clients[self.ROOM].device.details)  # straight from the address
        self.assertEqual(len(self.warnings), 1)
        self.assertIn("monitor: not found (no Bluetooth LE device was heard at all", self.warnings[0])

    async def test_scanned_strips_are_used_as_found(self):
        g = await self.connect(True, heard=[(self.MON.addr, "MELK-OA10 22"), ("11:22:33:44:55:66", "phone")],
                               known=set())
        self.assertEqual(g.live, [self.MON])
        self.assertEqual(g.clients[self.MON].device.details, "scan record")
        self.assertEqual(len(self.warnings), 1)  # the room strip: not heard, and Windows never saw it
        self.assertIn("room: not found (switched off, out of range", self.warnings[0])

    async def test_linux_only_connects_to_scanned_strips(self):
        g = await self.connect(False, heard=[], known={self.ROOM.addr})
        self.assertEqual(g.live, [])
        self.assertEqual(FakeBleakClient.made, [])  # BlueZ needs the scan record to connect
        self.assertEqual(len(self.warnings), 2)


@unittest.skipUnless(HAVE_DEPS, "needs numpy and Pillow")
class WindowsUI(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        asyncio.get_running_loop().slow_callback_duration = 2  # Textual start-up
        import lightui as U
        from test_ui import DEVICES, FakeGroup
        self.U, self.devices = U, DEVICES
        self.tmp = tempfile.TemporaryDirectory()
        self.saved = (U.SETTINGS_FILE, L.Group, L.screen_sync, L.IS_WINDOWS, W.list_monitors, W.list_windows,
                      sys.modules.get("soundcard"))
        U.SETTINGS_FILE = os.path.join(self.tmp.name, "ui.json")
        L.Group, L.IS_WINDOWS = FakeGroup, True
        sys.modules["soundcard"] = fake_soundcard()
        W.list_monitors = lambda: [
            {"index": 1, "width": 2560, "height": 1440, "left": 0, "top": 0, "primary": True, "name": "Dell"},
            {"index": 2, "width": 1920, "height": 1080, "left": 2560, "top": 0, "primary": False, "name": ""}]
        W.list_windows = lambda: [{"hwnd": 7, "title": "Big Buck Bunny - VLC media player", "rect": (0, 0, 800, 600)}]
        self.targets = []

        async def fake_screen(*args, **kw):
            self.targets.append(args[11])
            await asyncio.Event().wait()
        L.screen_sync = fake_screen

    async def asyncTearDown(self):
        (self.U.SETTINGS_FILE, L.Group, L.screen_sync, L.IS_WINDOWS, W.list_monitors, W.list_windows, sc) = self.saved
        if sc is None:
            sys.modules.pop("soundcard", None)
        else:
            sys.modules["soundcard"] = sc
        L.log = lambda msg, err=True: None
        self.tmp.cleanup()

    async def test_pick_window_and_monitor(self):
        app = self.U.LightUI(list(self.devices))
        async with app.run_test(size=(110, 50)) as pilot:
            await pilot.pause(0.3)
            await pilot.press("4")
            await pilot.pause(0.2)
            sources = [v for _, v in app.source_options()]
            self.assertIn(MIC, sources)  # the webcam mic is offered in the music tab
            app.query_one("#screen-monitor").value = 2
            await pilot.pause(0.1)
            app.query_one("#screen-on").value = True
            await pilot.pause(0.3)
            self.assertEqual(self.targets[-1], "monitor:2")
            await pilot.press("x")
            await pilot.pause(0.2)
            app.query_one("#sc-window").value = True
            app.query_one("#screen-window").value = "Big Buck Bunny - VLC media player"
            await pilot.pause(0.2)
            app.query_one("#screen-on").value = True
            await pilot.pause(0.3)
            self.assertEqual(self.targets[-1], "window:Big Buck Bunny - VLC media player")
            await pilot.press("q")
            await pilot.pause(0.2)


if __name__ == "__main__":
    unittest.main()

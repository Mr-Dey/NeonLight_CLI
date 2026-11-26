"""Byte-for-byte checks of every frame against the Magic Lantern app (wl.smartled.rgb 6.11.13).

Each expected value is transcribed from com.easylink.colorful.service.BluetoothLEService
(Java bytes are signed: -1 = ff, -17 = ef, -125 = 83, -126 = 82, -127 = 81).
Run:  .venv/bin/python -m unittest -v
"""
import asyncio
import datetime as dt
import unittest

import lightctl as L


def java(*vals: int) -> bytes:
    return bytes(v & 0xFF for v in vals)


class Frames(unittest.TestCase):
    def test_power(self):  # lightOn()
        self.assertEqual(L.cmd_power(True), java(126, 4, 4, 1, 0, 1, -1, 0, -17))
        self.assertEqual(L.cmd_power(False), java(126, 4, 4, 0, 0, 0, -1, 0, -17))

    def test_brightness(self):  # changeBrightness(); light mode 255 is the app's default extra
        self.assertEqual(L.cmd_brightness(37), java(126, 4, 1, 37, 255, -1, -1, 0, -17))
        self.assertEqual(L.cmd_brightness(80, 1), java(126, 4, 1, 80, 1, -1, -1, 0, -17))

    def test_color(self):  # changeColor()
        self.assertEqual(L.cmd_color(0x12, 0x34, 0x56), java(126, 7, 5, 3, 0x12, 0x34, 0x56, 16, -17))

    def test_color_wiring_remap(self):  # this strip: byte1 drives green, byte2 blue, byte3 red
        self.assertEqual(L.cmd_color(255, 0, 0, order="gbr")[4:7], bytes([0, 0, 255]))
        self.assertEqual(L.cmd_color(255, 255, 0, order="gbr")[4:7], bytes([255, 0, 255]))

    def test_music(self):  # music_amplitude()
        self.assertEqual(L.cmd_music(0xAA, 0xBB, 0xCC), java(126, 7, 5, 3, 0xAA, 0xBB, 0xCC, 32, -17))

    def test_temp(self):  # changeColorTemperature(); ColorChangeFragment sends (100 - p, p)
        self.assertEqual(L.cmd_temp(30), java(126, 6, 5, 2, 70, 30, -1, 8, -17))

    def test_mode_and_speed(self):  # changeMode(), changeModeSpeed()
        self.assertEqual(L.cmd_mode(0xC1), java(126, 5, 3, 0xC1, 6, -1, -1, 0, -17))
        self.assertEqual(L.cmd_speed(100), java(126, 4, 2, 100, -1, -1, -1, 0, -17))

    def test_controller_mic(self):  # changeExternalMicOnOff/EqMode/Sensitive()
        self.assertEqual(L.cmd_mic(True), java(126, 4, 7, 1, -1, -1, -1, 0, -17))
        self.assertEqual(L.cmd_mic(False), java(126, 4, 7, 0, -1, -1, -1, 0, -17))
        self.assertEqual(L.cmd_mic_style(L.MIC_STYLES["rolling"]), java(126, 7, 3, 3 + 128, 4, -1, -1, 0, -17))
        self.assertEqual(L.cmd_mic_sens(50), java(126, 4, 6, 50, -1, -1, -1, 0, -17))

    def test_wiring(self):  # changePinSequence(); app option "G R B" = "102" -> digits + 1
        self.assertEqual(L.cmd_wiring("grb"), java(126, 6, -127, 2, 1, 3, -1, 0, -17))  # the monitor strip
        self.assertEqual(L.cmd_wiring("gbr"), java(126, 6, -127, 2, 3, 1, -1, 0, -17))
        self.assertEqual(L.cmd_wiring("rgb"), java(126, 6, -127, 1, 2, 3, -1, 0, -17))  # no re-mapping
        for physical in ("rgb", "rbg", "grb", "gbr", "brg", "bgr"):
            seq = L.cmd_wiring(physical)[3:6]
            for j in range(3):  # output j (LEDs of colour physical[j]) is fed colour seq[j]
                self.assertEqual("rgb"[seq[j] - 1], physical[j])

    def test_leds(self):  # changeSymphonyPoint()
        self.assertEqual(L.cmd_leds(300), java(126, 7, 33, 300 & 255, 300 >> 8, 0, -1, 0, -17))

    def test_clock(self):  # sendSystemTime(); Android Time.weekDay 0 = Sunday
        wed = dt.datetime(2026, 11, 25, 21, 5, 9)  # a Wednesday -> 3
        self.assertEqual(L.cmd_clock(wed), java(126, 7, -125, 21, 5, 9, 3, -1, -17))
        sun = dt.datetime(2026, 11, 29, 8, 0, 0)
        self.assertEqual(L.cmd_clock(sun)[6], 0)

    def test_timer(self):  # sendTimingStatus(); TimingFragment: (weeks & 127) | on << 7, mode 0/1
        self.assertEqual(L.cmd_timer(L.TIMER_ON, 7, 30, 0x1F, True), java(126, 8, -126, 7, 30, 0, 0, 0x9F, -17))
        self.assertEqual(L.cmd_timer(L.TIMER_OFF, 23, 0, 0x7F, False), java(126, 8, -126, 23, 0, 0, 1, 0x7F, -17))
        self.assertEqual(L.cmd_timer_query(1), java(126, 8, -126, 255, 255, 255, 1, 0, -17))

    def test_days(self):
        self.assertEqual(L.parse_days("weekdays"), 0b0011111)
        self.assertEqual(L.parse_days("mon-fri"), 0b0011111)
        self.assertEqual(L.parse_days("sat,sun"), 0b1100000)
        self.assertEqual(L.parse_days("fri-mon"), 0b1110001)


class Modes(unittest.TestCase):
    def test_count_and_ids(self):
        self.assertEqual(len(L.MODES), 213)
        self.assertEqual(len({m for _, m in L.MODES.values()}), 213)  # every id distinct

    def test_spot_checks(self):  # values from res/values/arrays.xml
        self.assertEqual(L.MODES["7-color-jump"], ("Basic", 0xC1))
        self.assertEqual(L.MODES["7-color-race"], ("Basic", 0x4D))
        self.assertEqual(L.MODES["w-r-w-flow"], ("Flow", 0x8F))
        self.assertEqual(L.parse_mode("run:12"), 0x6F)
        self.assertEqual(L.parse_mode("basic:1"), 0x00)


class MusicLogic(unittest.TestCase):
    def test_palette_is_apps(self):  # Utils.musicColors = -65536, -16711936, -16776961, -256, -65281, -16711681, -1
        argb = [-65536, -16711936, -16776961, -256, -65281, -16711681, -1]
        self.assertEqual(L.MUSIC_PALETTE, [((c >> 16) & 255, (c >> 8) & 255, c & 255) for c in argb])

    def test_mic_react(self):
        r = L.MicReact()
        self.assertTrue(all(r(50.0) is None for _ in range(20)))  # learns the room for 20 frames
        self.assertIs(r(52.0), False)  # within 5 dB of background -> dark
        self.assertIs(r(60.0), True)   # 10 dB above -> next colour

    def test_auto_gate(self):
        g = L.AutoGate(fps=10)
        self.assertTrue(all(g(45.0) is None for _ in range(9)))  # ~1 s learning the room
        self.assertIs(g(45.5), False)  # room noise -> dark
        self.assertIs(g(60.0), True)   # music well above the room -> colour
        self.assertIs(g(float("-inf")), False)  # digital silence never counts as sound

    def test_level_norm(self):
        n = L.LevelNorm(fps=10)
        for _ in range(30):
            n(50.0)
        self.assertEqual(n(50.0), 0.0)        # at the quiet level
        self.assertEqual(n(80.0), 1.0)        # a new peak
        self.assertEqual(n(float("-inf")), 0.0)

    def test_resolve_effect(self):
        self.assertEqual(L.resolve_effect("7-color-running"), ("7-color-running", 0x67))
        self.assertEqual(L.resolve_effect("run:1"), ("red-running", 0x59))
        name, mid = L.resolve_effect("random")
        self.assertEqual(L.MODES[name][1], mid)

    def test_level_db(self):
        import array
        self.assertEqual(L.level_db(array.array("h", [0] * 100).tobytes()), float("-inf"))
        self.assertAlmostEqual(L.level_db(array.array("h", [1000, -1000] * 50).tobytes()), 60.0)


class Screen(unittest.TestCase):
    @staticmethod
    def frame(fill, bars=0):
        w, h = L.SCREEN_W, L.SCREEN_H
        out = bytearray()
        for y in range(h):
            px = (0, 0, 0) if y < bars or y >= h - bars else fill
            out += bytes(px) * w
        return bytes(out)

    def test_black_screen(self):
        self.assertIsNone(L.screen_color(self.frame((0, 0, 0))))

    def test_vivid_colours_full_brightness(self):
        for fill in ((255, 0, 0), (0, 255, 0), (0, 0, 255)):
            out = L.screen_color(self.frame(fill), saturation=1.0, floor=0)
            self.assertEqual(tuple(round(v) for v in out), fill)

    def test_letterbox_keeps_hue(self):
        r, g, b = L.screen_color(self.frame((0, 0, 255), bars=6), saturation=1.0, floor=0)
        self.assertGreater(b, 150)
        self.assertLess(max(r, g), 5)

    def test_brightness_gain(self):
        dim = L.screen_color(self.frame((100, 0, 0)), saturation=1.0, floor=0, gain=1.0)
        lifted = L.screen_color(self.frame((100, 0, 0)), saturation=1.0, floor=0, gain=2.5)
        self.assertAlmostEqual(dim[0], 100, delta=2)
        self.assertGreater(lifted[0], 240)

    def test_colour_boost_saturates(self):
        r, g, b = L.screen_color(self.frame((200, 150, 150)), saturation=4.0, floor=0, gain=1.0)
        self.assertLess(min(g, b), 5)  # a pale pink becomes a vivid red

    @staticmethod
    def split(top, bottom, top_rows):
        w, h = L.SCREEN_W, L.SCREEN_H
        return bytes(top) * (w * top_rows) + bytes(bottom) * (w * (h - top_rows))

    def test_main_colour_beats_small_vivid_spot(self):
        frame = self.split((255, 0, 0), (60, 90, 200), 5)   # small pure red strip, big soft blue
        r, g, b = L.screen_color(frame, saturation=1.0, floor=0, gain=1.0, mode="dominant")
        self.assertGreater(b, 2 * r)                         # the strip shows the blue
        r2, g2, b2 = L.screen_color(frame, saturation=1.0, floor=0, gain=1.0, mode="vivid")
        self.assertGreater(r2 / b2, r / b)                   # vivid lets the red pull it

    def test_mostly_white_scene_stays_white(self):
        frame = self.split((255, 0, 0), (235, 235, 235), 2)  # a little red on a white page
        r, g, b = L.screen_color(frame, saturation=1.0, floor=0, gain=1.0, mode="dominant")
        self.assertGreater(min(g, b), 0.8 * r)

    def test_led_gamma_keeps_orange_orange(self):
        frame = self.frame((255, 128, 0))
        lin = L.screen_color(frame, saturation=1.0, floor=0, gain=1.0, led_gamma=1.0)
        old = L.screen_color(frame, saturation=1.0, floor=0, gain=1.0, led_gamma=2.4)
        self.assertAlmostEqual(lin[1], 255 * 0.2158, delta=4)  # the real light share of green
        self.assertAlmostEqual(old[1], 255 * 0.2158 ** (1 / 2.4), delta=6)  # paler, yellower

    def test_main_colour_does_not_flip_on_one_frame(self):
        s = L.ScreenSampler("dominant", saturation=1.0, floor=0, gain=1.0)
        red_more = self.split((255, 0, 0), (0, 0, 255), 15)   # 15 of 27 rows red
        blue_more = self.split((255, 0, 0), (0, 0, 255), 12)
        for _ in range(6):
            s(red_more)
        r, g, b = s(blue_more)  # one frame with slightly more blue doesn't switch the light
        self.assertGreater(r, b)

    def test_dark_scene_dims_to_floor(self):
        r, g, b = L.screen_color(self.frame((20, 20, 20)), floor=5)
        self.assertLess(max(r, g, b), 40)


class FakeClient:
    def __init__(self):
        self.writes = []

    async def write_gatt_char(self, char, data, response):
        self.writes.append((bytes(data), response))


class GroupWrites(unittest.TestCase):
    def group(self, *orders):
        g = L.Group([])
        for i, order in enumerate(orders):
            d = L.Device(f"s{i}", f"AA:BB:CC:DD:EE:0{i}", order)
            g.devices.append(d)
            g.clients[d] = FakeClient()
        return g

    def run_async(self, coro):
        import asyncio
        asyncio.run(coro)

    def test_same_moment_each_strips_own_wiring(self):
        g = self.group("gbr", "rgb")
        self.run_async(g.write(lambda d: L.cmd_color(255, 0, 0, order=d.order)))
        a, b = (c.writes[0][0] for c in g.clients.values())
        self.assertEqual(a[4:7], bytes([0, 0, 255]))   # gbr strip: red is the 3rd byte
        self.assertEqual(b[4:7], bytes([255, 0, 0]))   # rgb strip: red is the 1st byte

    def test_plain_bytes_go_to_everyone(self):
        g = self.group("gbr", "gbr")
        self.run_async(g.write(L.cmd_power(True)))
        self.assertTrue(all(c.writes == [(L.cmd_power(True), True)] for c in g.clients.values()))


class TimedLink(FakeClient):
    """A fake strip whose acknowledged writes take `rtt` seconds; remembers when each frame was sent."""

    def __init__(self, rtt):
        super().__init__()
        self.rtt, self.sent = rtt, []

    async def write_gatt_char(self, char, data, response):
        import time
        self.sent.append((time.monotonic(), data[0]))
        await super().write_gatt_char(char, data, response)
        if response:
            await asyncio.sleep(self.rtt)


class Feed(unittest.TestCase):
    """Two strips with different link speeds: neither may build a backlog, each is paced by its own
    link, and with alignment both show each frame at (nearly) the same moment."""

    def run_feed(self, align, ack_every):
        import time

        async def go():
            g = L.Group([])
            fast, slow = L.Device("fast", "AA:BB:CC:DD:EE:01", "rgb"), L.Device("slow", "AA:BB:CC:DD:EE:02", "rgb")
            g.devices += [fast, slow]
            g.clients = {fast: TimedLink(0.02), slow: TimedLink(0.10)}
            born = {}
            async with L.LiveFeed(g, ack_every=ack_every, align=align) as feed:
                for i in range(150):  # a new frame every 10 ms for 1.5 s
                    born[i % 256] = time.monotonic()
                    feed.push(bytes([i % 256]))
                    await asyncio.sleep(0.01)
            # an acknowledged write reaches the strip halfway through its round trip
            late = {d.name: [t + c.rtt / 2 - born[i] for t, i in c.sent[3:]] for d, c in g.clients.items()}
            return ({k: sum(v) / len(v) for k, v in late.items()},
                    {d.name: len(c.sent) for d, c in g.clients.items()})
        return asyncio.run(go())

    def test_no_backlog_on_the_slow_link(self):
        late, counts = self.run_feed(align=False, ack_every=1)
        self.assertLess(late["slow"], 0.05 + 0.03)  # always the newest frame: half a round trip, no queue
        self.assertGreater(counts["fast"], 2 * counts["slow"])  # each link runs at its own pace

    def test_strips_change_together(self):
        unaligned, _ = self.run_feed(align=False, ack_every=1)
        aligned, _ = self.run_feed(align=True, ack_every=1)
        self.assertGreater(abs(unaligned["fast"] - unaligned["slow"]), 0.03)  # 20 ms vs 100 ms links
        self.assertLess(abs(aligned["fast"] - aligned["slow"]), 0.015)

    def test_paced_writes_follow_each_link(self):
        _, counts = self.run_feed(align=False, ack_every=8)
        # slow link: one write per 0.10 / 2 / 0.8 = 62.5 ms (plus its acknowledged ones) in 1.5 s
        self.assertTrue(15 <= counts["slow"] <= 30, counts)
        self.assertGreater(counts["fast"], 3 * counts["slow"])


class Devices(unittest.TestCase):
    def setUp(self):
        import tempfile
        self.tmp = tempfile.TemporaryDirectory()
        self.old = L.DEVICES_FILE
        L.DEVICES_FILE = f"{self.tmp.name}/devices.json"
        L.save_devices({"monitor": L.Device("monitor", "BE:69:32:13:B6:00", "gbr"),
                        "room": L.Device("room", "BE:69:04:14:32:04", "rgb")})

    def tearDown(self):
        L.DEVICES_FILE = self.old
        self.tmp.cleanup()

    def test_default_is_every_saved_strip(self):
        import os
        env = os.environ.pop("LIGHTCTL_ADDR", None)
        try:
            self.assertEqual([d.name for d in L.resolve_targets(None)], ["monitor", "room"])
        finally:
            if env is not None:
                os.environ["LIGHTCTL_ADDR"] = env

    def test_names_macs_and_override(self):
        self.assertEqual([d.name for d in L.resolve_targets("room")], ["room"])
        self.assertEqual(L.resolve_targets("be:69:32:13:b6:00")[0].name, "monitor")  # MAC of a saved strip
        self.assertEqual([d.order for d in L.resolve_targets("all", "brg")], ["brg", "brg"])
        self.assertEqual(len(L.resolve_targets("monitor,all")), 2)  # no duplicates

    def test_unknown_name(self):
        with self.assertRaises(SystemExit):
            L.resolve_targets("kitchen")

if __name__ == "__main__":
    unittest.main()

"""Byte-for-byte checks of every frame against the Magic Lantern app (wl.smartled.rgb 6.11.13).

Each expected value is transcribed from com.easylink.colorful.service.BluetoothLEService
(Java bytes are signed: -1 = ff, -17 = ef, -125 = 83, -126 = 82, -127 = 81).
Run:  .venv/bin/python -m unittest -v
"""
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

    def test_wiring(self):  # changePinSequence(); app option "G B R" = "120" -> digits + 1
        self.assertEqual(L.cmd_wiring("gbr"), java(126, 6, -127, 2, 3, 1, -1, 0, -17))
        self.assertEqual(L.cmd_wiring("rgb"), java(126, 6, -127, 1, 2, 3, -1, 0, -17))

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

    def test_dark_scene_dims_to_floor(self):
        r, g, b = L.screen_color(self.frame((20, 20, 20)), floor=5)
        self.assertLess(max(r, g, b), 40)


class StreamWriter(unittest.TestCase):
    def test_every_fourth_write_is_acknowledged(self):
        import asyncio

        class Fake:
            def __init__(self):
                self.acks = []

            async def write_gatt_char(self, char, data, response):
                self.acks.append(response)

        fake = Fake()
        s = L.Stream(fake, barrier=4)

        async def go():
            for _ in range(8):
                await s.send(b"x")
        asyncio.run(go())
        self.assertEqual(fake.acks, [False, False, False, True] * 2)


if __name__ == "__main__":
    unittest.main()

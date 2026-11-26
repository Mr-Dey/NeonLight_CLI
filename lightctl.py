#!/home/subhasish/projects/strip-light-cli/.venv/bin/python
"""lightctl - control a MELK / ELK-BLEDOM Bluetooth LED strip from Linux.

Protocol taken from the Magic Lantern app (wl.smartled.rgb 6.11.13,
com.easylink.colorful.service.BluetoothLEService) and verified on a MELK-OA10.
GATT service fff0, write characteristic fff3. Every command is 9 bytes:

    7e <len> <cmd> ...payload... ef

    power on/off    7e 04 04 01 00 01 ff 00 ef  /  7e 04 04 00 00 00 ff 00 ef
    colour          7e 07 05 03 RR GG BB 10 ef
    music frame     7e 07 05 03 RR GG BB 20 ef      streamed ~10/s by the app's Music & phone-mic modes
    brightness      7e 04 01 BB LM ff ff 00 ef      BB 0-100, LM light mode (1 RGB, 2 W, 3 CT, ff any)
    colour temp     7e 06 05 02 WW CC ff 08 ef      WW = 100 - CC; app sends it only to MELK-*CT* models
    effect          7e 05 03 MM 06 ff ff 00 ef      MM from MODES below
    effect speed    7e 04 02 SS ff ff ff 00 ef      SS 0-100
    controller mic  7e 04 07 on ff ff ff 00 ef / style 7e 07 03 (80+n) 04 ff ff 00 ef / sens 7e 04 06 SS ff ff ff 00 ef
    wiring order    7e 06 81 a b c ff 00 ef         stored on the controller
    LED count       7e 07 21 lo hi 00 ff 00 ef      stored on the controller
    clock           7e 07 83 HH MM SS WD ff ef      WD 0=Sunday
    timer           7e 08 82 HH MM SS T  DD ef      T 0=on-timer 1=off-timer, DD bit0=Mon..bit6=Sun, bit7=enabled
                    (HH MM SS = ff ff ff queries the timer; read fff3 afterwards for the answer)
    scene           7e 05 31 SS 07 ff ff 01 ef      MELK-OC/OT only - no effect on this strip
"""
from __future__ import annotations

import argparse
import array
import asyncio
import collections
import colorsys
import datetime as dt
import json
import math
import os
import random
import re
import subprocess
import sys

from bleak import BleakClient, BleakScanner

DEFAULT_ADDR = "BE:69:32:13:B6:00"
# Channel wiring of this strip, verified by hand: byte1 lights green, byte2 blue, byte3 red.
DEFAULT_ORDER = "gbr"
WRITE_CHAR = "0000fff3-0000-1000-8000-00805f9b34fb"
NAME_PREFIXES = ("ELK", "MELK", "LEDBLE", "BLEDOM")

COLORS = {
    "red": (255, 0, 0), "green": (0, 255, 0), "blue": (0, 0, 255),
    "white": (255, 255, 255), "yellow": (255, 255, 0), "cyan": (0, 255, 255),
    "magenta": (255, 0, 255), "purple": (128, 0, 255), "orange": (255, 80, 0),
    "pink": (255, 20, 100), "warm": (255, 140, 40),
}

# (group, id) per effect - extracted from Magic Lantern 6.11.13 res/values/arrays.xml.
# Group 'Scenes' is omitted: the app only enables it for MELK-OC/OT or OA10 firmware YH3016FG24R24V37.
MODES: dict[str, tuple[str, int]] = {
    # --- Basic ---
    'auto-play': ('Basic', 0x00),
    'magic-forward': ('Basic', 0x01),
    'magic-back': ('Basic', 0x02),
    '7-color-energy': ('Basic', 0xd4),
    '7-color-jump': ('Basic', 0xc1),
    'r-g-b-jump': ('Basic', 0xc2),
    'y-c-p-jump': ('Basic', 0xc3),
    '7-color-strobe': ('Basic', 0xc4),
    'r-g-b-strobe': ('Basic', 0xc5),
    'y-c-p-strobe': ('Basic', 0xc6),
    '7-color-gradual': ('Basic', 0xc7),
    'r-y-gradual': ('Basic', 0xc8),
    'r-p-gradual': ('Basic', 0xc9),
    'g-c-gradual': ('Basic', 0xca),
    'g-y-gradual': ('Basic', 0xcb),
    'b-p-gradual': ('Basic', 0xcc),
    'red-marquee': ('Basic', 0xcd),
    'green-marquee': ('Basic', 0xce),
    'blue-marquee': ('Basic', 0xcf),
    'yellow-marquee': ('Basic', 0xd0),
    'cyan-marquee': ('Basic', 0xd1),
    'purple-marquee': ('Basic', 0xd2),
    'white-marquee': ('Basic', 0xd3),
    '7-color-race': ('Basic', 0x4d),
    '7-color-race-back': ('Basic', 0x4e),
    'r-g-b-race': ('Basic', 0x4f),
    'r-g-b-race-back': ('Basic', 0x50),
    'y-c-p-race': ('Basic', 0x51),
    'y-c-p-race-back': ('Basic', 0x52),
    '7-color-wave': ('Basic', 0x53),
    '7-color-wave-back': ('Basic', 0x54),
    'r-g-b-wave': ('Basic', 0x55),
    'r-g-b-wave-back': ('Basic', 0x56),
    'y-c-p-wave': ('Basic', 0x57),
    'y-c-p-wave-back': ('Basic', 0x58),
    '7-color-flush': ('Basic', 0xb5),
    '7-color-flush-back': ('Basic', 0xb6),
    'r-g-b-flush': ('Basic', 0xb7),
    'r-g-b-flush-back': ('Basic', 0xb8),
    'y-c-p-flush': ('Basic', 0xb9),
    'y-c-p-flush-back': ('Basic', 0xba),
    '7-color-flush-close': ('Basic', 0xbb),
    '7-color-flush-open': ('Basic', 0xbc),
    'r-g-b-flush-close': ('Basic', 0xbd),
    'r-g-b-flush-open': ('Basic', 0xbe),
    'y-c-p-flush-close': ('Basic', 0xbf),
    'y-c-p-flush-open': ('Basic', 0xc0),
    # --- Curtain ---
    '7-color-close': ('Curtain', 0x39),
    '7-color-open': ('Curtain', 0x3a),
    'r-g-b-close': ('Curtain', 0x3b),
    'r-g-b-open': ('Curtain', 0x3c),
    'y-c-p-close': ('Curtain', 0x3d),
    'y-c-p-open': ('Curtain', 0x3e),
    'red-close': ('Curtain', 0x3f),
    'red-open': ('Curtain', 0x40),
    'green-close': ('Curtain', 0x41),
    'green-open': ('Curtain', 0x42),
    'blue-close': ('Curtain', 0x43),
    'blue-open': ('Curtain', 0x44),
    'yellow-close': ('Curtain', 0x45),
    'yellow-open': ('Curtain', 0x46),
    'cyan-close': ('Curtain', 0x47),
    'cyan-open': ('Curtain', 0x48),
    'purple-close': ('Curtain', 0x49),
    'purple-open': ('Curtain', 0x4a),
    'white-close': ('Curtain', 0x4b),
    'white-open': ('Curtain', 0x4c),
    # --- Trans ---
    '7-color-trans': ('Trans', 0x03),
    '7-color-trans-back': ('Trans', 0x04),
    'r-g-b-trans': ('Trans', 0x05),
    'r-g-b-trans-back': ('Trans', 0x06),
    'y-c-p-trans': ('Trans', 0x07),
    'y-c-p-trans-back': ('Trans', 0x08),
    '6-color-to-red': ('Trans', 0x09),
    '6-color-to-red-back': ('Trans', 0x0a),
    '6-color-to-green': ('Trans', 0x0b),
    '6-color-to-green-back': ('Trans', 0x0c),
    '6-color-to-blue': ('Trans', 0x0d),
    '6-color-to-blue-back': ('Trans', 0x0e),
    '6-color-to-cyan': ('Trans', 0x0f),
    '6-color-to-cyan-back': ('Trans', 0x10),
    '6-color-to-yellow': ('Trans', 0x11),
    '6-color-to-yellow-back': ('Trans', 0x12),
    '6-color-to-purple': ('Trans', 0x13),
    '6-color-to-purple-back': ('Trans', 0x14),
    '6-color-to-white': ('Trans', 0x15),
    '6-color-to-white-back': ('Trans', 0x16),
    # --- Water ---
    '7-color-water': ('Water', 0x27),
    '7-color-water-back': ('Water', 0x28),
    'r-g-b-water': ('Water', 0x29),
    'r-g-b-water-back': ('Water', 0x2a),
    'y-c-p-water': ('Water', 0x2b),
    'y-c-p-water-back': ('Water', 0x2c),
    'r-g-water': ('Water', 0x2d),
    'r-g-water-back': ('Water', 0x2e),
    'g-b-water': ('Water', 0x2f),
    'g-b-water-back': ('Water', 0x30),
    'y-b-water': ('Water', 0x31),
    'y-b-water-back': ('Water', 0x32),
    'y-c-water': ('Water', 0x33),
    'y-c-water-back': ('Water', 0x34),
    'c-p-water': ('Water', 0x35),
    'c-p-water-back': ('Water', 0x36),
    'white-water': ('Water', 0x37),
    'white-water-back': ('Water', 0x38),
    # --- Flow ---
    'w-r-w-flow': ('Flow', 0x8f),
    'w-r-w-flow-back': ('Flow', 0x90),
    'w-g-w-flow': ('Flow', 0x91),
    'w-g-w-flow-back': ('Flow', 0x92),
    'w-b-w-flow': ('Flow', 0x93),
    'w-b-w-flow-back': ('Flow', 0x94),
    'w-y-w-flow': ('Flow', 0x95),
    'w-y-w-flow-back': ('Flow', 0x96),
    'w-c-w-flow': ('Flow', 0x97),
    'w-c-w-flow-back': ('Flow', 0x98),
    'w-p-w-flow': ('Flow', 0x99),
    'w-p-w-flow-back': ('Flow', 0x9a),
    'r-w-r-flow': ('Flow', 0x9b),
    'r-w-r-flow-back': ('Flow', 0x9c),
    'g-w-g-flow': ('Flow', 0x9d),
    'g-w-g-flow-back': ('Flow', 0x9e),
    'b-w-b-flow': ('Flow', 0x9f),
    'b-w-b-flow-back': ('Flow', 0xa0),
    'y-w-y-flow': ('Flow', 0xa1),
    'y-w-y-flow-back': ('Flow', 0xa2),
    'c-w-c-flow': ('Flow', 0xa3),
    'c-w-c-flow-back': ('Flow', 0xa4),
    'p-w-p-flow': ('Flow', 0xa5),
    'p-w-p-flow-back': ('Flow', 0xa6),
    # --- Tail ---
    '7-color-tail': ('Tail', 0x17),
    '7-color-tail-back': ('Tail', 0x18),
    'red-tail': ('Tail', 0x19),
    'red-tail-back': ('Tail', 0x1a),
    'green-tail': ('Tail', 0x1b),
    'green-tail-back': ('Tail', 0x1c),
    'blue-tail': ('Tail', 0x1d),
    'blue-tail-back': ('Tail', 0x1e),
    'yellow-tail': ('Tail', 0x1f),
    'yellow-tail-back': ('Tail', 0x20),
    'cyan-tail': ('Tail', 0x21),
    'cyan-tail-back': ('Tail', 0x22),
    'purple-tail': ('Tail', 0x23),
    'purple-tail-back': ('Tail', 0x24),
    'white-tail': ('Tail', 0x25),
    'white-tail-back': ('Tail', 0x26),
    # --- Run ---
    'red-running': ('Run', 0x59),
    'green-running': ('Run', 0x5b),
    'blue-running': ('Run', 0x5d),
    'yellow-running': ('Run', 0x5f),
    'cyan-running': ('Run', 0x61),
    'purple-running': ('Run', 0x63),
    'white-running': ('Run', 0x65),
    '7-color-running': ('Run', 0x67),
    'r-g-b-running': ('Run', 0x69),
    'y-c-p-running': ('Run', 0x6b),
    'b-p-c-y-running': ('Run', 0x6d),
    'b-g-c-y-running': ('Run', 0x6f),
    'red-dot-in-white-running': ('Run', 0x71),
    'green-dot-in-red-running': ('Run', 0x73),
    'blue-dot-in-green-running': ('Run', 0x75),
    'yellow-dot-in-blue-running': ('Run', 0x77),
    'cyan-dot-in-yellow-running': ('Run', 0x79),
    'purple-dot-in-cyan-running': ('Run', 0x7b),
    'white-dot-in-purple-running': ('Run', 0x7d),
    'white-dot-in-red-running': ('Run', 0x7f),
    '7-color-in-red-running': ('Run', 0x81),
    '7-color-in-green-running': ('Run', 0x83),
    '7-color-in-blue-running': ('Run', 0x85),
    '7-color-in-yellow-running': ('Run', 0x87),
    '7-color-in-cyan-running': ('Run', 0x89),
    '7-color-in-purple-running': ('Run', 0x8b),
    '7-color-in-white-running': ('Run', 0x8d),
    'green-dot-in-blue-running': ('Run', 0xa7),
    'green-dot-in-red-running-2': ('Run', 0xa9),
    'red-dot-in-blue-running': ('Run', 0xab),
    'cyan-dot-in-yellow-running-2': ('Run', 0xad),
    'yellow-dot-in-purple-running': ('Run', 0xaf),
    'white-dot-in-yelllow-running': ('Run', 0xb1),
    'yellow-dot-in-white-running': ('Run', 0xb3),
    # --- RunBack ---
    'red-run-back': ('RunBack', 0x5a),
    'green-run-back': ('RunBack', 0x5c),
    'blue-run-back': ('RunBack', 0x5e),
    'yellow-run-back': ('RunBack', 0x60),
    'cyan-run-back': ('RunBack', 0x62),
    'purple-run-back': ('RunBack', 0x64),
    'white-run-back': ('RunBack', 0x66),
    '7-color-run-back': ('RunBack', 0x68),
    'r-g-b-run-back': ('RunBack', 0x6a),
    'y-c-p-run-back': ('RunBack', 0x6c),
    'b-p-c-y-run-back': ('RunBack', 0x6e),
    'b-g-c-y-run-back': ('RunBack', 0x70),
    'red-dot-in-white-run-back': ('RunBack', 0x72),
    'green-dot-in-red-run-back': ('RunBack', 0x74),
    'blue-dot-in-green-run-back': ('RunBack', 0x76),
    'yellow-dot-in-blue-run-back': ('RunBack', 0x78),
    'cyan-dot-in-yellow-run-back': ('RunBack', 0x7a),
    'purple-dot-in-cyan-run-back': ('RunBack', 0x7c),
    'white-dot-in-purple-run-back': ('RunBack', 0x7e),
    'white-dot-in-red-run-back': ('RunBack', 0x80),
    '7-color-in-red-run-back': ('RunBack', 0x82),
    '7-color-in-green-run-back': ('RunBack', 0x84),
    '7-color-in-blue-run-back': ('RunBack', 0x86),
    '7-color-in-yellow-run-back': ('RunBack', 0x88),
    '7-color-in-cyan-run-back': ('RunBack', 0x8a),
    '7-color-in-purple-run-back': ('RunBack', 0x8c),
    '7-color-in-white-run-back': ('RunBack', 0x8e),
    'green-dot-in-blue-run-back': ('RunBack', 0xa8),
    'green-dot-in-red-run-back-2': ('RunBack', 0xaa),
    'red-dot-in-blue-run-back': ('RunBack', 0xac),
    'cyan-dot-in-yellow-run-back-2': ('RunBack', 0xae),
    'yellow-dot-in-purple-run-back': ('RunBack', 0xb0),
    'white-dot-in-yelllow-run-back': ('RunBack', 0xb2),
    'yellow-dot-in-white-run-back': ('RunBack', 0xb4),
}


# ---------- frame builders (byte-for-byte what the app sends) ----------

def cmd_power(on: bool) -> bytes:
    v = 1 if on else 0
    return bytes([0x7E, 0x04, 0x04, v, 0x00, v, 0xFF, 0x00, 0xEF])


def _wired(r: int, g: int, b: int, order: str) -> tuple[int, int, int]:
    """order = which colour each of the 3 payload bytes drives on the strip's wiring."""
    ch = {"r": r, "g": g, "b": b}
    x, y, z = (ch[c] for c in order)
    return x, y, z


def cmd_color(r: int, g: int, b: int, order: str = "rgb") -> bytes:
    x, y, z = _wired(r, g, b, order)
    return bytes([0x7E, 0x07, 0x05, 0x03, x, y, z, 0x10, 0xEF])


def cmd_music(r: int, g: int, b: int, order: str = "rgb") -> bytes:
    """The frame the app streams in Music / phone-mic mode (same as colour, but 0x20 instead of 0x10)."""
    x, y, z = _wired(r, g, b, order)
    return bytes([0x7E, 0x07, 0x05, 0x03, x, y, z, 0x20, 0xEF])


def cmd_temp(cold: int) -> bytes:
    """Colour temperature: cold 0 = warmest, 100 = coldest (the app sends warm = 100 - cold)."""
    return bytes([0x7E, 0x06, 0x05, 0x02, 100 - cold, cold, 0xFF, 0x08, 0xEF])


MIC_STYLES = {"energic": 0, "rhythm": 1, "spectrum": 2, "rolling": 3,
              "energic2": 4, "rhythm2": 5, "spectrum2": 6, "rolling2": 7}


def cmd_mic(on: bool) -> bytes:
    return bytes([0x7E, 0x04, 0x07, 1 if on else 0, 0xFF, 0xFF, 0xFF, 0x00, 0xEF])


def cmd_mic_style(style: int) -> bytes:
    return bytes([0x7E, 0x07, 0x03, 0x80 + style, 0x04, 0xFF, 0xFF, 0x00, 0xEF])


def cmd_mic_sens(pct: int) -> bytes:
    return bytes([0x7E, 0x04, 0x06, pct, 0xFF, 0xFF, 0xFF, 0x00, 0xEF])


def cmd_wiring(order: str) -> bytes:
    """Stored wiring order, as the app's pin-sequence dialog sends it ("G B R" -> 02 03 01)."""
    a, b, c = ("rgb".index(ch) + 1 for ch in order)
    return bytes([0x7E, 0x06, 0x81, a, b, c, 0xFF, 0x00, 0xEF])


def cmd_leds(count: int) -> bytes:
    """Number of addressable points ("symphony points"; the app accepts 10-1000)."""
    return bytes([0x7E, 0x07, 0x21, count & 0xFF, (count >> 8) & 0xFF, 0x00, 0xFF, 0x00, 0xEF])


def cmd_clock(now: dt.datetime) -> bytes:
    weekday = (now.weekday() + 1) % 7  # Android Time.weekDay: 0 = Sunday
    return bytes([0x7E, 0x07, 0x83, now.hour, now.minute, now.second, weekday, 0xFF, 0xEF])


TIMER_ON, TIMER_OFF = 0, 1
DAY_NAMES = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]  # bit 0..6, as in the app's WeekSelectView


def cmd_timer(which: int, hour: int, minute: int, days: int, enabled: bool) -> bytes:
    flags = (days & 0x7F) | (0x80 if enabled else 0)
    return bytes([0x7E, 0x08, 0x82, hour, minute, 0, which, flags, 0xEF])


def cmd_timer_query(which: int) -> bytes:
    return bytes([0x7E, 0x08, 0x82, 0xFF, 0xFF, 0xFF, which, 0x00, 0xEF])


def cmd_brightness(pct: int, light_mode: int = 0xFF) -> bytes:
    # light_mode: the app sends 01 from its RGB colour page, ff from the effects page
    return bytes([0x7E, 0x04, 0x01, pct, light_mode, 0xFF, 0xFF, 0x00, 0xEF])


def cmd_mode(mode_id: int) -> bytes:
    return bytes([0x7E, 0x05, 0x03, mode_id, 0x06, 0xFF, 0xFF, 0x00, 0xEF])


def cmd_speed(pct: int) -> bytes:
    return bytes([0x7E, 0x04, 0x02, pct, 0xFF, 0xFF, 0xFF, 0x00, 0xEF])


# ---------- argument parsing ----------

def parse_color(s: str) -> tuple[int, int, int]:
    s = s.strip().lower()
    if s in COLORS:
        return COLORS[s]
    if "," in s:
        try:
            parts = [int(p) for p in s.split(",")]
        except ValueError:
            parts = []
        if len(parts) != 3 or not all(0 <= p <= 255 for p in parts):
            raise argparse.ArgumentTypeError("R,G,B must be three numbers 0-255")
        return parts[0], parts[1], parts[2]
    h = s.lstrip("#")
    if len(h) == 3:
        h = "".join(c * 2 for c in h)
    if len(h) == 6:
        try:
            r, g, b = bytes.fromhex(h)
            return r, g, b
        except ValueError:
            pass
    raise argparse.ArgumentTypeError(
        f"bad colour {s!r}: use a name ({', '.join(COLORS)}), hex (ff8800) or R,G,B")


def parse_pct(s: str) -> int:
    try:
        v = int(s)
    except ValueError:
        raise argparse.ArgumentTypeError("must be a number 0-100") from None
    if not 0 <= v <= 100:
        raise argparse.ArgumentTypeError("must be 0-100")
    return v


def group_lists() -> dict[str, list[tuple[str, int]]]:
    """Effects per app tab, in the app's display order (the app numbers them from 1)."""
    out: dict[str, list[tuple[str, int]]] = {}
    for name, (group, mid) in MODES.items():
        out.setdefault(group.lower(), []).append((name, mid))
    return out


def mode_matches(filter_: str | None, name: str, group: str) -> bool:
    if not filter_:
        return True
    f = filter_.lower()
    return f in name or f == group.lower()


def parse_mode(s: str) -> int:
    key = s.strip().lower()
    if key in MODES:
        return MODES[key][1]
    if key == "random":
        return random.choice(list(MODES.values()))[1]
    if ":" in key:  # app-style "tab:number", e.g. run:12
        grp, _, idx = key.partition(":")
        lst = group_lists().get(grp)
        if lst and idx.isdigit() and 1 <= int(idx) <= len(lst):
            return lst[int(idx) - 1][1]
        tabs = ", ".join(f"{g}:1-{len(v)}" for g, v in group_lists().items())
        raise argparse.ArgumentTypeError(f"bad tab:number {s!r}; valid: {tabs}")
    try:
        v = int(key, 0)
    except ValueError:
        matches = [k for k in MODES if key in k]
        hint = f" Did you mean: {', '.join(matches[:6])}?" if matches else ""
        raise argparse.ArgumentTypeError(f"unknown mode {s!r}.{hint} See --list-modes.") from None
    if not 0 <= v <= 0xFF:
        raise argparse.ArgumentTypeError("mode id must be 0-255")
    return v


def parse_order(s: str) -> str:
    s = s.strip().lower()
    if sorted(s) != ["b", "g", "r"]:
        raise argparse.ArgumentTypeError("order must be a permutation of r, g, b (e.g. rgb, grb, gbr)")
    return s


def parse_leds(s: str) -> int:
    try:
        v = int(s)
    except ValueError:
        raise argparse.ArgumentTypeError("LED count must be a number") from None
    if not 10 <= v <= 1000:
        raise argparse.ArgumentTypeError("LED count must be 10-1000 (the app's range)")
    return v


def parse_timer(s: str) -> tuple[int, int] | str:
    """'HH:MM' -> (hour, minute); 'off' -> 'off' (disable)."""
    s = s.strip().lower()
    if s == "off":
        return "off"
    m = re.fullmatch(r"(\d{1,2}):(\d{2})", s)
    if not m or int(m[1]) > 23 or int(m[2]) > 59:
        raise argparse.ArgumentTypeError("timer must be HH:MM (24h) or 'off'")
    return int(m[1]), int(m[2])


def parse_days(s: str) -> int:
    s = s.strip().lower()
    presets = {"daily": 0x7F, "everyday": 0x7F, "weekdays": 0x1F, "weekends": 0x60}
    if s in presets:
        return presets[s]
    mask = 0
    for part in s.split(","):
        if "-" in part:  # range like mon-fri
            a, _, b = part.partition("-")
            if a not in DAY_NAMES or b not in DAY_NAMES:
                raise argparse.ArgumentTypeError(f"bad day range {part!r}")
            i, j = DAY_NAMES.index(a), DAY_NAMES.index(b)
            for k in range(i, j + 1) if i <= j else [*range(i, 7), *range(0, j + 1)]:
                mask |= 1 << k
        elif part in DAY_NAMES:
            mask |= 1 << DAY_NAMES.index(part)
        else:
            raise argparse.ArgumentTypeError(
                f"bad day {part!r}: use daily, weekdays, weekends, or e.g. mon,wed,fri / mon-fri")
    return mask


def days_str(mask: int) -> str:
    mask &= 0x7F
    if mask == 0x7F:
        return "daily"
    if mask == 0x1F:
        return "weekdays"
    if mask == 0x60:
        return "weekends"
    return ",".join(d for i, d in enumerate(DAY_NAMES) if mask >> i & 1) or "no days"


def parse_raw(s: str) -> bytes:
    try:
        return bytes.fromhex(s.replace(":", " "))
    except ValueError:
        raise argparse.ArgumentTypeError("raw payload must be hex, e.g. '7e 04 04 01 00 01 ff 00 ef'") from None


def list_modes(filter_: str | None) -> None:
    for group, items in group_lists().items():
        shown = [(i, n, m) for i, (n, m) in enumerate(items, 1)
                 if mode_matches(filter_, n, MODES[n][0])]
        if not shown:
            continue
        print(f"\n{MODES[items[0][0]][0]}  (use {group}:N or the name)")
        for i, name, mid in shown:
            print(f"  {i:>3}  {name:<34} 0x{mid:02x}")


async def tour(addr: str, filter_: str | None, interval: float, speed: int | None,
               duration: float) -> None:
    """Play effects one after another, printing each, so you can find the ones you like."""
    items = []
    for group, lst in group_lists().items():
        for i, (name, mid) in enumerate(lst, 1):
            if mode_matches(filter_, name, MODES[name][0]):
                items.append((f"{group}:{i}", name, mid))
    if not items:
        raise SystemExit(f"lightctl: no effects match {filter_!r} (see --list-modes)")
    async with BleakClient(addr, timeout=15) as client:
        await asyncio.sleep(0.3)
        w = lambda pkt: client.write_gatt_char(WRITE_CHAR, pkt, response=True)  # noqa: E731
        await w(cmd_power(True))
        if speed is not None:
            await w(cmd_speed(speed))
        loop = asyncio.get_running_loop()
        t0 = loop.time()
        print(f"touring {len(items)} effects, {interval:g}s each - Ctrl-C keeps the current one",
              file=sys.stderr)
        try:
            for n, (ref, name, mid) in enumerate(items, 1):
                if duration and loop.time() - t0 >= duration:
                    break
                print(f"[{n:>3}/{len(items)}] {ref:<12} {name:<34} 0x{mid:02x}", flush=True)
                await w(cmd_mode(mid))
                await asyncio.sleep(interval)
        except asyncio.CancelledError:
            pass


# ---------- BLE ----------

async def scan(seconds: float) -> None:
    print(f"Scanning {seconds:.0f}s for LED controllers...")
    found = await BleakScanner.discover(timeout=seconds, return_adv=True)
    hits = [(d, adv) for d, adv in found.values()
            if (d.name or "").upper().startswith(NAME_PREFIXES)]
    if not hits:
        print("No ELK/MELK-style devices found. Is the phone app still connected to it?")
        return
    for d, adv in sorted(hits, key=lambda x: -x[1].rssi):
        print(f"  {d.address}  {d.name!r}  RSSI {adv.rssi}")


async def send(addr: str, packets: list[tuple[str, bytes]], verbose: bool, retries: int = 3) -> None:
    last_err: Exception | None = None
    for attempt in range(1, retries + 1):
        try:
            async with BleakClient(addr, timeout=15) as client:
                await asyncio.sleep(0.3)  # let the controller settle after connect
                for label, pkt in packets:
                    if verbose:
                        print(f"{label:<12} {pkt.hex(' ')}")
                    # acknowledged write, like the app (WRITE_TYPE_DEFAULT); unacked writes got dropped
                    await client.write_gatt_char(WRITE_CHAR, pkt, response=True)
                    await asyncio.sleep(0.1)
                return
        except Exception as e:  # noqa: BLE001 - bleak raises a mix of types
            last_err = e
            if verbose:
                print(f"attempt {attempt} failed: {e}", file=sys.stderr)
            await asyncio.sleep(1)
    raise SystemExit(f"lightctl: could not reach {addr}: {last_err}\n"
                     "Hint: close the phone app (the strip accepts only one connection).")


# ---------- clock & timers (stored on the controller; read back the way the app does) ----------

async def _read_frame(client: BleakClient, cmd: int, length: int) -> bytes | None:
    """The app waits 1 s after a query, then reads fff3; the controller answers with a 9-byte frame."""
    await asyncio.sleep(1.0)
    v = bytes(await client.read_gatt_char(WRITE_CHAR))
    if len(v) >= 9 and v[0] == 0x7E and v[1] == length and v[2] == cmd:
        return v[:9]
    return None


async def _query_timer(client: BleakClient, which: int) -> bytes | None:
    await client.write_gatt_char(WRITE_CHAR, cmd_timer_query(which), response=True)
    v = await _read_frame(client, 0x82, 0x08)
    return v if v is not None and v[6] == which and v[8] == 0xEF else None


def _timer_text(v: bytes | None) -> str:
    if v is None:
        return "no answer from controller"
    h, m, flags = v[3], v[4], v[7]
    if h > 23 or m > 59:
        return f"not set ({v.hex(' ')})"
    state = "enabled" if flags & 0x80 else "disabled"
    return f"{h:02d}:{m:02d}  {days_str(flags)}  ({state})"


async def timers(addr: str, set_on, set_off, days: int, verbose: bool) -> None:
    """Show both timers; set or disable them first if asked. Syncs the clock like the app does."""
    async with BleakClient(addr, timeout=15) as client:
        await asyncio.sleep(0.3)
        w = lambda pkt: client.write_gatt_char(WRITE_CHAR, pkt, response=True)  # noqa: E731
        await w(cmd_clock(dt.datetime.now()))
        for which, label, new in ((TIMER_ON, "on-timer ", set_on), (TIMER_OFF, "off-timer", set_off)):
            if new is not None:
                if new == "off":  # keep the stored time and days, clear the enabled bit
                    cur = await _query_timer(client, which)
                    if cur is not None and cur[3] <= 23 and cur[4] <= 59:
                        pkt = cmd_timer(which, cur[3], cur[4], cur[7], False)
                    else:
                        pkt = cmd_timer(which, 0, 0, days, False)
                else:
                    pkt = cmd_timer(which, new[0], new[1], days, True)
                if verbose:
                    print(f"set {label.strip():<10} {pkt.hex(' ')}")
                await w(pkt)
            v = await _query_timer(client, which)
            if verbose and v is not None:
                print(f"read {label.strip():<9} {v.hex(' ')}")
            print(f"{label}: {_timer_text(v)}")


async def sync_clock(addr: str, verbose: bool) -> None:
    async with BleakClient(addr, timeout=15) as client:
        await asyncio.sleep(0.3)
        pkt = cmd_clock(dt.datetime.now())
        if verbose:
            print(f"clock      {pkt.hex(' ')}")
        await client.write_gatt_char(WRITE_CHAR, pkt, response=True)
        v = await _read_frame(client, 0x83, 0x07)
        if v is None:
            print("clock sent (controller did not echo it back)")
        else:
            day = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"][v[6] % 7]
            print(f"controller clock: {day} {v[3]:02d}:{v[4]:02d}:{v[5]:02d}")


# ---------- music mode (what the app's Music / phone-mic tabs stream) ----------

MUSIC_PALETTE = [(255, 0, 0), (0, 255, 0), (0, 0, 255), (255, 255, 0),
                 (255, 0, 255), (0, 255, 255), (255, 255, 255)]  # Utils.musicColors, in order
AUDIO_RATE = 8000  # the app records at 8 kHz mono 16-bit


def _pactl(*args: str) -> str:
    try:
        return subprocess.run(["pactl", *args], capture_output=True, text=True, check=True).stdout
    except (OSError, subprocess.CalledProcessError) as e:
        raise SystemExit(f"lightctl: cannot query PulseAudio/PipeWire: {e}") from None


def audio_sources() -> list[dict]:
    """Every audio input: microphones, and 'monitors' that carry what the laptop is playing."""
    out = []
    for s in json.loads(_pactl("-f", "json", "list", "sources")):
        props = s.get("properties", {})
        monitor = props.get("device.class") == "monitor" or s["name"].endswith(".monitor")
        out.append({"name": s["name"], "desc": s.get("description") or s["name"], "monitor": monitor})
    return out


def list_sources() -> None:
    srcs = audio_sources()
    default_mic = _pactl("get-default-source").strip()
    default_mon = _pactl("get-default-sink").strip() + ".monitor"
    preferred = os.environ.get("LIGHTCTL_MIC")
    print("Audio inputs for --music (use the number, or any part of the name):")
    for i, s in enumerate(srcs, 1):
        kind = "laptop audio" if s["monitor"] else "microphone"
        tags = []
        if s["name"] == default_mon:
            tags.append("used by --music / --music system")
        if s["name"] == default_mic and not preferred:
            tags.append("used by --music mic")
        print(f"  {i:>2}  {kind:<12}  {s['desc']}" + (f"   <- {'; '.join(tags)}" if tags else ""))
        print(f"      {'':<12}  {s['name']}")
    if preferred:
        print(f"\nLIGHTCTL_MIC={preferred!r} -> used by --music mic")
    print("\nexamples:  lightctl --music 2     lightctl --music webcam     export LIGHTCTL_MIC=logitech")


def pulse_source(source: str) -> tuple[str, str]:
    """'system' | 'mic' | number | name | part of a name -> (source name, default reaction style)."""
    if source == "system":
        return _pactl("get-default-sink").strip() + ".monitor", "music"
    if source == "mic":
        preferred = os.environ.get("LIGHTCTL_MIC")
        if not preferred:
            return _pactl("get-default-source").strip(), "mic"
        source = preferred
    srcs = audio_sources()
    if source.isdigit():
        i = int(source)
        if not 1 <= i <= len(srcs):
            raise SystemExit(f"lightctl: no audio input #{i}; see lightctl --list-sources")
        pick = srcs[i - 1]
    else:
        exact = [s for s in srcs if s["name"] == source]
        hits = exact or [s for s in srcs if source.lower() in (s["name"] + " " + s["desc"]).lower()]
        if len(hits) > 1:  # a name can match a device's mic and its output monitor: prefer the mic
            mics = [s for s in hits if not s["monitor"]]
            hits = mics if len(mics) == 1 else hits
        if not hits:
            raise SystemExit(f"lightctl: no audio input matches {source!r}. Is it plugged in? "
                             "See lightctl --list-sources")
        if len(hits) > 1:
            names = "\n  ".join(f"{s['desc']}  ({s['name']})" for s in hits)
            raise SystemExit(f"lightctl: {source!r} matches several inputs, be more specific:\n  {names}")
        pick = hits[0]
    return pick["name"], "music" if pick["monitor"] else "mic"


def level_db(buf: bytes) -> float:
    """10*log10(mean square of 16-bit samples) - the same number the app's AudioRecorderService computes."""
    a = array.array("h")
    a.frombytes(buf)
    if sys.byteorder != "little":
        a.byteswap()
    ms = sum(s * s for s in a) / max(1, len(a))
    return 10 * math.log10(ms) if ms > 0 else float("-inf")


class MicReact:
    """The app's phone-mic logic (StreamingFragment.calculateVolume): track the background level and
    flash the next colour whenever the sound is at least 5 dB above it, otherwise go dark."""

    def __init__(self) -> None:
        self.background: list[float] = []

    def __call__(self, d: float) -> bool | None:
        d2 = d if d >= 0 else 0.0
        if len(self.background) < 200:  # learning the room first (~2 s): send nothing
            self.background.extend([d2] * 10)
            return None
        acc = 0
        for v in self.background:  # integer accumulation, exactly like the app
            acc = int(acc + v)
        avg = acc // len(self.background)
        if d < avg + 5:
            for _ in range(5):
                self.background.insert(random.randrange(200), d2)
            while len(self.background) > 2000:  # the app lets this grow forever; bound it
                del self.background[random.randrange(len(self.background))]
            return False
        return True


class AutoGate:
    """Music-tab style for microphones: a new colour on every frame that is at least `margin` dB above
    the quietest level heard in the last `window` seconds, dark otherwise. (The app's Music tab does the
    same thing with its own player, where silence is exact; a microphone never hears true silence.)"""

    def __init__(self, fps: float, window: float = 30.0, margin: float = 3.0) -> None:
        self.hist: collections.deque[float] = collections.deque(maxlen=max(10, int(fps * window)))
        self.margin = margin
        self.learn = max(5, int(fps))  # ~1 s before the first frame

    def floor(self) -> float:
        return min(self.hist) if self.hist else float("-inf")

    def __call__(self, d: float) -> bool | None:
        if d != float("-inf"):
            self.hist.append(d)
        if len(self.hist) < self.learn:
            return None
        return d >= self.floor() + self.margin


async def music(addr: str, source: str, react: str | None, fps: float, palette: list,
                threshold: float | None, order: str, duration: float, verbose: bool) -> None:
    dev, kind = pulse_source(source)  # kind: 'music' for laptop-audio monitors, 'mic' for microphones
    react = react or "music"
    chunk = max(1, round(AUDIO_RATE / fps))
    gate = AutoGate(fps) if react == "music" and kind == "mic" and threshold is None else None
    if threshold is None:
        threshold = 40.0
    proc = await asyncio.create_subprocess_exec(
        "parec", "--raw", "--format=s16le", f"--rate={AUDIO_RATE}", "--channels=1",
        f"--latency-msec={max(10, round(500 / fps))}", "-d", dev,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    mic = MicReact() if react == "mic" else None
    idx, frames = 0, 0
    try:
        async with BleakClient(addr, timeout=15) as client:
            await asyncio.sleep(0.3)
            await client.write_gatt_char(WRITE_CHAR, cmd_power(True), response=True)
            print(f"music mode ({react} style) listening to {dev} at {fps:g} frames/s - Ctrl-C to stop",
                  file=sys.stderr)
            loop = asyncio.get_running_loop()
            t0 = loop.time()
            while not duration or loop.time() - t0 < duration:
                buf = await proc.stdout.readexactly(chunk * 2)
                d = level_db(buf)
                if mic is not None:
                    loud = mic(d)
                    if loud is None:
                        continue
                elif gate is not None:  # Music-tab logic on a microphone: above the room's quiet level
                    loud = gate(d)
                    if loud is None:
                        continue
                else:  # Music-tab logic: a new colour every frame unless (near) silent
                    loud = d >= threshold
                if loud:
                    idx = (idx + 1) % len(palette)  # the app advances the palette, then uses it
                    rgb = palette[idx]
                else:
                    rgb = (0, 0, 0)
                # unacknowledged: 10+ frames/s is more than acked writes can carry; a lost frame is replaced next tick
                await client.write_gatt_char(WRITE_CHAR, cmd_music(*rgb, order=order), response=False)
                frames += 1
                if verbose:
                    bar = "#" * max(0, min(40, int((d - 20) / 2))) if d > -1e9 else ""
                    gate_txt = f" floor {gate.floor():5.1f}" if gate is not None else ""
                    print(f"{d:6.1f} dB{gate_txt} {bar:<40} {'%02x%02x%02x' % rgb}", file=sys.stderr)
    except asyncio.IncompleteReadError:
        err = (await proc.stderr.read()).decode(errors="replace").strip()
        raise SystemExit(f"lightctl: audio capture from {dev!r} stopped: {err or 'parec exited'}") from None
    except asyncio.CancelledError:
        pass
    finally:
        if proc.returncode is None:
            proc.terminate()
            await proc.wait()
        if verbose:
            print(f"{frames} music frames sent", file=sys.stderr)


# ---------- host-driven animations (laptop streams colours over one connection) ----------

def _hsv(h: float, s: float = 1.0, v: float = 1.0) -> tuple[int, int, int]:
    r, g, b = colorsys.hsv_to_rgb(h % 1.0, s, v)
    return round(r * 255), round(g * 255), round(b * 255)


def _scale(c: tuple[int, int, int], k: float) -> tuple[int, int, int]:
    return round(c[0] * k), round(c[1] * k), round(c[2] * k)


def anim_rainbow(t: float, period: float, base):
    return _hsv(t / period)


def anim_breathe(t: float, period: float, base):
    k = 0.03 + 0.97 * math.sin(math.pi * t / period) ** 2
    return _scale(base, k)


def anim_fire(t: float, period: float, base):
    # orange-red flicker; random walk smoothed by the frame rate
    flick = random.uniform(0.45, 1.0)
    return round(255 * flick), round(random.uniform(40, 110) * flick), 0


def anim_police(t: float, period: float, base):
    phase = (t / period) % 1.0
    # two quick flashes red, two quick flashes blue
    slot = int(phase * 8)
    if slot in (0, 2):
        return 255, 0, 0
    if slot in (4, 6):
        return 0, 0, 255
    return 0, 0, 0


ANIMATIONS = {
    #  name      (function,      default period s, default colour)
    "rainbow": (anim_rainbow, 6.0, None),
    "breathe": (anim_breathe, 4.0, COLORS["warm"]),
    "fire":    (anim_fire,    1.0, None),
    "police":  (anim_police,  1.2, None),
}


async def animate(addr: str, name: str, duration: float, period: float | None,
                  color: tuple[int, int, int] | None, order: str, fps: float, verbose: bool,
                  fast: bool = False) -> None:
    fn, default_period, default_color = ANIMATIONS[name]
    period = period or default_period
    base = color or default_color or (255, 255, 255)
    async with BleakClient(addr, timeout=15) as client:
        await asyncio.sleep(0.3)
        w = lambda pkt: client.write_gatt_char(WRITE_CHAR, pkt, response=True)  # noqa: E731
        await w(cmd_power(True))
        await w(cmd_color(*_scale(base, 0.0) if name == "breathe" else base, order=order))
        await w(cmd_brightness(100, 0x01))  # also drops out of any built-in effect
        loop = asyncio.get_running_loop()
        t0 = loop.time()
        frames, last = 0, None
        print(f"animating {name!r} (period {period}s) - Ctrl-C to stop", file=sys.stderr)
        try:
            while True:
                now = loop.time() - t0
                if duration and now >= duration:
                    break
                rgb = fn(now, period, base)
                if rgb != last:
                    pkt = cmd_color(*rgb, order=order)
                    # --fast: unacknowledged frames (much higher rate; a dropped frame is overwritten by the next)
                    await client.write_gatt_char(WRITE_CHAR, pkt, response=not fast)
                    last = rgb
                    frames += 1
                nxt = t0 + (int(now * fps) + 1) / fps
                await asyncio.sleep(max(0.0, nxt - loop.time()))
        except asyncio.CancelledError:
            pass
        finally:
            el = loop.time() - t0
            if verbose and el > 0:
                print(f"{frames} frames in {el:.1f}s = {frames / el:.1f} fps", file=sys.stderr)


def main() -> None:
    p = argparse.ArgumentParser(
        prog="lightctl",
        description="Control a MELK / ELK-BLEDOM Bluetooth LED strip (everything the Magic Lantern app can do).",
        epilog="examples:\n"
               "  lightctl --power on --color red --brightness 60\n"
               "  lightctl --mode 7-color-race --speed 100    # built-in effect, full speed\n"
               "  lightctl --tour strobe                      # find effects you like\n"
               "  lightctl --music                            # react to what the laptop is playing\n"
               "  lightctl --music mic                        # react to the laptop microphone\n"
               "  lightctl --animate rainbow\n"
               "  lightctl --timer-on 07:30 --days weekdays --timer-off 23:00\n"
               "  lightctl --power off",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    b = p.add_argument_group("light")
    b.add_argument("--power", choices=["on", "off"])
    b.add_argument("--color", "--colour", "-c", type=parse_color, metavar="COLOR",
                   help="name, hex (ff8800 / #f80) or R,G,B")
    b.add_argument("--brightness", "-b", type=parse_pct, metavar="0-100")

    e = p.add_argument_group("built-in effects (run on the controller)")
    e.add_argument("--mode", "-m", type=parse_mode, metavar="MODE",
                   help="effect name, tab:number (run:12), id (0xc1) or 'random'")
    e.add_argument("--speed", "-s", type=parse_pct, metavar="0-100", help="effect speed (app default 100)")
    e.add_argument("--list-modes", nargs="?", const="", metavar="FILTER",
                   help="list effects, optionally filtered by word or tab")
    e.add_argument("--tour", nargs="?", const="", metavar="FILTER",
                   help="play effects one by one (all, a tab like 'water', or a word like 'strobe')")
    e.add_argument("--interval", "-i", type=float, default=8, help="seconds per effect in --tour (default 8)")

    m = p.add_argument_group("sound")
    m.add_argument("--music", nargs="?", const="system", metavar="SOURCE",
                   help="stream the app's music animation from laptop audio: 'system' (what is playing, "
                        "default), 'mic' (default mic, or $LIGHTCTL_MIC), or an input number / name / "
                        "part of a name from --list-sources (e.g. 'webcam', 'logitech')")
    m.add_argument("--list-sources", action="store_true", help="list microphones and other audio inputs")
    m.add_argument("--react", choices=["music", "mic"], default="music",
                   help="music (default) = app's Music tab: crazy-fast, a new colour every frame unless "
                        "quiet; mic = app's phone-mic mode: calmer, flashes only on sounds 5 dB above the room")
    m.add_argument("--palette", nargs="+", type=parse_color, metavar="COLOR",
                   help="colours to cycle in --music (default: the app's red green blue yellow magenta cyan white)")
    m.add_argument("--threshold", type=float,
                   help="--react music: fixed level (dB, the app's scale, about 0-90) below which the strip goes "
                        "dark. Default: 40 for laptop audio; for microphones it follows the room automatically")
    m.add_argument("--mic", choices=["on", "off"], help="the controller's own microphone mode")
    m.add_argument("--mic-style", choices=list(MIC_STYLES), help="controller mic effect style")
    m.add_argument("--mic-sens", type=parse_pct, metavar="0-100", help="controller mic sensitivity")

    a_ = p.add_argument_group("laptop-driven animation")
    a_.add_argument("--animate", "-a", choices=list(ANIMATIONS),
                    help="stream an animation; --color sets the base colour for breathe")
    a_.add_argument("--duration", "-d", type=float, default=0,
                    help="seconds to run --animate / --music / --tour (default: until Ctrl-C)")
    a_.add_argument("--period", type=float, help="seconds per animation cycle")
    a_.add_argument("--fps", type=float,
                    help="frames per second (default: 20 for --animate, 10 for --music like the app)")
    a_.add_argument("--fast", action="store_true",
                    help="--animate: don't wait for acks (about 20 fps instead of 6; may drop a frame)")

    s = p.add_argument_group("settings stored on the controller")
    s.add_argument("--set-wiring", type=parse_order, metavar="ORDER",
                   help="tell the controller the strip's channel order (the app's pin-sequence setting)")
    s.add_argument("--leds", type=parse_leds, metavar="N",
                   help="number of addressable points on the strip (10-1000)")
    s.add_argument("--sync-clock", action="store_true", help="set the controller clock to the laptop's")
    s.add_argument("--timer-on", type=parse_timer, metavar="HH:MM|off", help="daily switch-on time")
    s.add_argument("--timer-off", type=parse_timer, metavar="HH:MM|off", help="daily switch-off time")
    s.add_argument("--days", type=parse_days, default=0x7F,
                   help="days for --timer-on/--timer-off: daily (default), weekdays, weekends, mon,wed,fri, mon-fri")
    s.add_argument("--timers", action="store_true", help="show the timers stored on the controller")

    c = p.add_argument_group("connection & debugging")
    c.add_argument("--addr", default=os.environ.get("LIGHTCTL_ADDR", DEFAULT_ADDR),
                   help=f"device MAC (env LIGHTCTL_ADDR, default {DEFAULT_ADDR})")
    c.add_argument("--order", type=parse_order,
                   default=parse_order(os.environ.get("LIGHTCTL_ORDER", DEFAULT_ORDER)),
                   help=f"strip channel wiring used for colours (env LIGHTCTL_ORDER, default {DEFAULT_ORDER})")
    c.add_argument("--scan", action="store_true", help="find nearby LED controllers")
    c.add_argument("--raw", type=parse_raw, action="append", metavar="HEX",
                   help="send a raw 9-byte payload (repeatable)")
    c.add_argument("-v", "--verbose", action="store_true", help="print bytes sent / audio levels")
    a = p.parse_args()

    def run(coro, stopped_msg: str = "stopped") -> None:
        try:
            asyncio.run(coro)
        except KeyboardInterrupt:
            print(stopped_msg, file=sys.stderr)

    if a.list_modes is not None:
        list_modes(a.list_modes or None)
        return
    if a.list_sources:
        list_sources()
        return
    if a.scan:
        run(scan(8))
        return
    if a.animate:
        run(animate(a.addr, a.animate, a.duration, a.period, a.color, a.order,
                    a.fps or 20, a.verbose, a.fast))
        return
    if a.music:
        run(music(a.addr, a.music, a.react, a.fps or 10, a.palette or MUSIC_PALETTE,
                  a.threshold, a.order, a.duration, a.verbose))
        return
    if a.tour is not None:
        run(tour(a.addr, a.tour or None, a.interval, a.speed, a.duration),
            "stopped - the last effect listed keeps running")
        return
    if a.timers or a.timer_on is not None or a.timer_off is not None:
        run(timers(a.addr, a.timer_on, a.timer_off, a.days, a.verbose))
        return
    if a.sync_clock:
        run(sync_clock(a.addr, a.verbose))
        return

    packets: list[tuple[str, bytes]] = []
    if a.power == "on":
        packets.append(("power on", cmd_power(True)))
    if a.set_wiring:
        packets.append(("wiring", cmd_wiring(a.set_wiring)))
    if a.leds is not None:
        packets.append(("leds", cmd_leds(a.leds)))
    if a.color is not None:
        packets.append(("color", cmd_color(*a.color, order=a.order)))
    if a.mode is not None:
        packets.append(("mode", cmd_mode(a.mode)))
    if a.speed is not None:
        packets.append(("speed", cmd_speed(a.speed)))
    if a.brightness is not None:
        # light-mode byte as the app sends it: 1 from the RGB colour page, ff from the effects page
        if a.mode is None and a.color is not None:
            lm = 0x01
        else:
            lm = 0xFF
        packets.append(("brightness", cmd_brightness(a.brightness, lm)))
    if a.mic == "on":
        packets.append(("mic on", cmd_mic(True)))
    if a.mic_style is not None:
        packets.append(("mic style", cmd_mic_style(MIC_STYLES[a.mic_style])))
    elif a.mic == "on":
        packets.append(("mic style", cmd_mic_style(MIC_STYLES["rolling"])))  # the app's default style
    if a.mic_sens is not None:
        packets.append(("mic sens", cmd_mic_sens(a.mic_sens)))
    if a.mic == "off":
        packets.append(("mic off", cmd_mic(False)))
    for raw in a.raw or []:
        packets.append(("raw", raw))
    if a.power == "off":
        packets.append(("power off", cmd_power(False)))

    if not packets:
        p.print_help()
        return
    asyncio.run(send(a.addr, packets, a.verbose))


if __name__ == "__main__":
    main()

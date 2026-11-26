#!/usr/bin/env python3
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
    wiring order    7e 06 81 a b c ff 00 ef         stored; output j gets colour abc[j] (colours and effects)
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
import contextlib
import dataclasses
import datetime as dt
import json
import math
import os
import random
import re
import subprocess
import sys
import time

from bleak import BleakClient, BleakScanner


IS_WINDOWS = sys.platform == "win32"


def log(msg: str, err: bool = True) -> None:
    """Progress and warnings. The terminal UI (lightui.py) replaces this to show them in its own panel."""
    print(msg, file=sys.stderr if err else sys.stdout, flush=True)

DEFAULT_ADDR = "BE:69:32:13:B6:00"
# Colour order for strips saved without --order. Both strips have their wiring stored on the
# controller ("G R B" on the monitor strip, factory on the room strip), so plain rgb is right for both.
DEFAULT_ORDER = "rgb"
# Effects run on each controller with its own stored speed; with several strips we send this one
# (unless --speed is given) so they move alike. 80 is what the loved music-effects run used.
SYNC_SPEED = 80
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


def cmd_wiring(physical: str) -> bytes:
    """Store a strip's wiring on its controller (the app's pin-sequence setting, "G R B" -> 02 01 03).

    `physical` is the strip's real LED order, i.e. the --order you find after resetting the
    controller with --set-wiring rgb (01 02 03 = no re-mapping). The controller then feeds output j
    with colour seq[j], for plain colours and its own effects alike, so both come out right and
    the strip is used with --order rgb. (Verified on a MELK-OA10 that had "R B G" stored from before:
    it looked like 'gbr' but is really wired 'grb'.)
    """
    a, b, c = ("rgb".index(ch) + 1 for ch in physical)
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


def parse_capture(s: str) -> str:
    kind, sep, arg = s.strip().partition(":")
    kind = kind.lower()
    if kind not in ("monitor", "window") or (kind == "monitor" and sep and not arg.strip().isdigit()):
        raise argparse.ArgumentTypeError("use monitor, monitor:N, window or window:TITLE")
    return f"{kind}{sep}{arg.strip()}" if sep else kind


def list_windows() -> None:
    if not IS_WINDOWS:
        print("On Linux the desktop asks which monitor or window to share when screen sync starts.")
        return
    import winbackend
    print("Monitors (--capture monitor:N):")
    for m in winbackend.list_monitors():
        print(f"  {m['index']}  {m['width']}x{m['height']} at {m['left']},{m['top']}"
              + ("   primary - used by --capture monitor" if m["primary"] else "") + (f"   {m['name']}" if m["name"] else ""))
    print("\nWindows (--capture \"window:part of the title\"):")
    for w in winbackend.list_windows():
        print(f"  {w['title']}")


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


async def tour(devices: list[Device], filter_: str | None, interval: float, speed: int | None,
               duration: float, verbose: bool = False) -> None:
    """Play effects one after another, printing each, so you can find the ones you like."""
    items = []
    for group, lst in group_lists().items():
        for i, (name, mid) in enumerate(lst, 1):
            if mode_matches(filter_, name, MODES[name][0]):
                items.append((f"{group}:{i}", name, mid))
    if not items:
        raise SystemExit(f"lightctl: no effects match {filter_!r} (see --list-modes)")
    async with _connected(devices, verbose) as g:
        await g.write(cmd_power(True))
        if speed is None and len(g.live) > 1:
            speed = SYNC_SPEED
        if speed is not None:
            await g.write(cmd_speed(speed))
        loop = asyncio.get_running_loop()
        t0 = loop.time()
        log(f"touring {len(items)} effects, {interval:g}s each")
        try:
            for n, (ref, name, mid) in enumerate(items, 1):
                if duration and loop.time() - t0 >= duration:
                    break
                log(f"[{n:>3}/{len(items)}] {ref:<12} {name:<34} 0x{mid:02x}", err=False)
                await g.write(cmd_mode(mid))
                await asyncio.sleep(interval)
        except asyncio.CancelledError:
            pass


# ---------- devices: one or more strips, controlled together ----------

CONFIG_DIR = (os.path.join(os.environ.get("APPDATA") or os.path.expanduser("~"), "lightctl") if IS_WINDOWS else
              os.path.join(os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config"), "lightctl"))
DEVICES_FILE = os.path.join(CONFIG_DIR, "devices.json")
MAC_RE = re.compile(r"[0-9A-Fa-f]{2}(:[0-9A-Fa-f]{2}){5}")


@dataclasses.dataclass(frozen=True)
class Device:
    name: str
    addr: str
    order: str = DEFAULT_ORDER


def load_devices() -> dict[str, Device]:
    try:
        with open(DEVICES_FILE) as f:
            raw = json.load(f)
    except FileNotFoundError:
        return {}
    except (OSError, ValueError) as e:
        raise SystemExit(f"lightctl: cannot read {DEVICES_FILE}: {e}") from None
    return {name: Device(name, v["addr"].upper(), v.get("order", DEFAULT_ORDER))
            for name, v in raw.get("devices", {}).items()}


def save_devices(devs: dict[str, Device]) -> None:
    os.makedirs(os.path.dirname(DEVICES_FILE), exist_ok=True)
    data = {"devices": {d.name: {"addr": d.addr, "order": d.order} for d in devs.values()}}
    tmp = DEVICES_FILE + ".tmp"
    with open(tmp, "w") as f:
        json.dump(data, f, indent=2)
        f.write("\n")
    os.replace(tmp, DEVICES_FILE)


def resolve_targets(spec: str | None, order: str | None = None) -> list[Device]:
    """'monitor,room' / 'all' / MACs -> devices. Default: $LIGHTCTL_ADDR, else every saved strip,
    else the built-in DEFAULT_ADDR. An explicit --order overrides each strip's saved wiring."""
    saved = load_devices()
    if spec is None:
        spec = os.environ.get("LIGHTCTL_ADDR") or ("all" if saved else DEFAULT_ADDR)
    out: list[Device] = []
    for part in (p.strip() for p in spec.split(",")):
        if not part:
            continue
        if part.lower() == "all":
            if not saved:
                raise SystemExit("lightctl: no saved strips yet - see lightctl --add-device")
            out.extend(saved.values())
        elif part in saved:
            out.append(saved[part])
        elif MAC_RE.fullmatch(part):
            known = next((d for d in saved.values() if d.addr == part.upper()), None)
            out.append(known or Device(part.upper(), part.upper(),
                                       os.environ.get("LIGHTCTL_ORDER", DEFAULT_ORDER)))
        else:
            raise SystemExit(f"lightctl: unknown strip {part!r} "
                             f"(saved: {', '.join(saved) or 'none'}; or give a MAC address)")
    uniq: list[Device] = []
    for d in out:
        if all(d.addr != u.addr for u in uniq):
            uniq.append(d)
    if order:
        uniq = [dataclasses.replace(d, order=order) for d in uniq]
    return uniq


@contextlib.asynccontextmanager
async def _connected(target, verbose: bool = False):
    """A device list -> connect for the duration; an already connected Group (the UI) -> use it as is."""
    if isinstance(target, Group):
        yield target
    else:
        async with Group(target, verbose) as g:
            yield g


class Group:
    """Connections to one or more strips. Every write goes to all of them at the same moment
    (colours re-ordered for each strip's wiring). A strip that can't be reached, or drops out,
    is skipped with a warning while the others carry on."""

    def __init__(self, devices: list[Device], verbose: bool = False) -> None:
        self.devices, self.verbose = devices, verbose
        self.clients: dict[Device, BleakClient] = {}
        self.muted: set[str] = set()  # strip names temporarily left out (the UI's per-strip switches)
        self.delivered: dict[str, int] = {}  # stream frames each strip received (the UI shows the rate)

    @property
    def live(self) -> list[Device]:
        return [d for d in self.devices if d in self.clients]

    def names(self) -> str:
        return ", ".join(d.name for d in self.live)

    @staticmethod
    def _warn(d: Device, msg: str) -> None:
        log(f"lightctl: {d.name}: {msg}")

    async def __aenter__(self) -> "Group":
        await self._find_and_connect(self.devices)
        if not self.clients:
            raise SystemExit("lightctl: could not reach any strip.\n"
                             "Hint: close the phone app (each strip accepts only one connection).")
        await asyncio.sleep(0.3)  # let the controllers settle after connecting
        return self

    async def connect_missing(self) -> list[Device]:
        """Try again for strips that are not connected (switched on later, back in range...)."""
        missing = [d for d in self.devices if d not in self.clients]
        if missing:
            await self._find_and_connect(missing)
            await asyncio.sleep(0.3)
        return [d for d in missing if d in self.clients]

    def prune(self) -> list[Device]:
        """Forget strips whose connection has gone (e.g. switched off); returns them."""
        gone = [d for d, c in self.clients.items() if not c.is_connected]
        for d in gone:
            self.clients.pop(d, None)
        return gone

    async def _find_and_connect(self, devices: list[Device]) -> None:
        want = {d.addr for d in devices}
        found: dict[str, object] = {}
        all_found = asyncio.Event()

        def seen(dev, adv) -> None:
            a = dev.address.upper()
            if a in want and a not in found:
                found[a] = dev
                if len(found) == len(want):
                    all_found.set()

        async with BleakScanner(detection_callback=seen):  # one scan finds every strip
            try:
                await asyncio.wait_for(all_found.wait(), timeout=10)
            except asyncio.TimeoutError:
                pass
        for d in devices:
            if d.addr not in found:
                self._warn(d, "not found (switched off, out of range, or connected to the phone app?)")
        await asyncio.gather(*(self._connect(d, found[d.addr]) for d in devices if d.addr in found))

    async def _connect(self, d: Device, ble_device, retries: int = 3) -> None:
        last: Exception | None = None
        for attempt in range(retries):
            client = BleakClient(ble_device, timeout=15)
            try:
                await client.connect()
                self.clients[d] = client
                return
            except Exception as e:  # noqa: BLE001 - bleak raises a mix of types
                last = e
                if self.verbose:
                    self._warn(d, f"connect attempt {attempt + 1} failed: {e}")
                await asyncio.sleep(0.5 + attempt)
        self._warn(d, f"could not connect: {last}")

    async def __aexit__(self, *exc) -> None:
        await asyncio.gather(*(c.disconnect() for c in self.clients.values()), return_exceptions=True)

    async def write(self, pkt, ack: bool = True) -> None:
        """pkt: the same bytes for every strip, or a function Device -> bytes (colours)."""
        async def one(d: Device, c: BleakClient) -> None:
            try:
                await c.write_gatt_char(WRITE_CHAR, pkt(d) if callable(pkt) else pkt, response=ack)
            except Exception as e:  # noqa: BLE001
                self.clients.pop(d, None)
                self._warn(d, f"dropped: {e}")

        await asyncio.gather(*(one(d, c) for d, c in list(self.clients.items()) if d.name not in self.muted))
        if not self.clients:
            raise SystemExit("lightctl: lost the connection to every strip")


class LiveFeed:
    """Continuous streams (screen sync, music, animations) to every strip, in step.

    Every strip has its own sender that always takes the newest frame, paced by its own link, so no
    strip ever carries a backlog. (Sending each frame to every strip at one shared pace left the slower
    link with a standing queue, which made that strip trail the other.)

    The link takes about one write per connection event, and an acknowledged write's round trip is
    about two events. So one write in `ack_every` is acknowledged to measure that round trip, and the
    writes in between are spaced at UTILISATION of what the link can carry: frames go out almost as
    fast as possible but never queue up. ack_every=1 acknowledges every write (slower, no estimate).
    With `align`, a strip whose link answers faster is handed a correspondingly older frame (up to
    MAX_SKEW), so all strips change colour at the same moment.
    """

    MAX_SKEW = 0.3
    UTILISATION = 0.8

    def __init__(self, g: Group, ack_every: int = 8, align: bool = True) -> None:
        self.g, self.ack_every, self.align = g, max(1, ack_every), align
        self.frames: collections.deque = collections.deque()  # (time, pkt), oldest first
        self.rtt: dict[Device, float] = {}
        self.tasks: dict[Device, asyncio.Task] = {}
        self.wake: dict[Device, asyncio.Event] = {}
        self.closed = False
        self._hi_timer = False
        g.delivered = {}

    async def __aenter__(self) -> "LiveFeed":
        if IS_WINDOWS and not self._hi_timer:
            import winbackend
            self._hi_timer = winbackend.timer_resolution(True)
        return self

    async def __aexit__(self, *exc) -> None:
        await self.close()

    def push(self, pkt) -> None:
        """A new frame: the same bytes for every strip, or a function Device -> bytes (colours)."""
        if IS_WINDOWS and not self._hi_timer and not self.closed:
            import winbackend
            self._hi_timer = winbackend.timer_resolution(True)
        now = time.monotonic()
        self.frames.append((now, pkt))
        while len(self.frames) > 2 and now - self.frames[1][0] > self.MAX_SKEW + 0.5:
            self.frames.popleft()
        for d, c in list(self.g.clients.items()):
            self.wake.setdefault(d, asyncio.Event()).set()
            task = self.tasks.get(d)
            if task is None or task.done():
                self.tasks[d] = asyncio.create_task(self._sender(d, c))

    def latency(self, d: Device) -> float:
        """Typical delay until this strip shows a frame: on average half a connection event when
        writes are paced, a whole one when every write waits for its acknowledgement."""
        return self.rtt[d] / (2 if self.ack_every == 1 else 4)

    def holdback(self, d: Device) -> float:
        """How much older a frame this strip gets, so it shows it when the slowest strip does."""
        if not self.align or d not in self.rtt:
            return 0.0
        lats = [self.latency(dev) for dev in self.rtt if dev in self.g.clients and dev.name not in self.g.muted]
        return min(self.MAX_SKEW, max(0.0, max(lats) - self.latency(d))) if lats else 0.0

    def _pick(self, d: Device) -> tuple:
        cutoff = time.monotonic() - self.holdback(d)
        pick = self.frames[0]
        for frame in self.frames:  # oldest -> newest: the newest one that is old enough
            if frame[0] > cutoff:
                break
            pick = frame
        return pick

    async def _sender(self, d: Device, c: BleakClient) -> None:
        sent_t: float | None = None
        last_send, n = 0.0, 0
        wake = self.wake.setdefault(d, asyncio.Event())
        while not self.closed and self.g.clients.get(d) is c:
            if d.name in self.g.muted or not self.frames:
                wake.clear()
                if not self.frames:
                    with contextlib.suppress(asyncio.TimeoutError):
                        await asyncio.wait_for(wake.wait(), timeout=0.05)
                else:
                    await asyncio.sleep(0.02)
                continue
            gap = self.rtt.get(d, 0.0) / 2 / self.UTILISATION  # one connection event, with headroom
            wait = last_send + gap - time.monotonic()
            if wait > 0:
                await asyncio.sleep(min(wait, 0.05))
                continue
            wake.clear()
            t, pkt = self._pick(d)
            if sent_t is not None and t <= sent_t:  # nothing newer ready for this strip yet
                cutoff = time.monotonic() - self.holdback(d)
                next_t = next((ft for ft, _ in self.frames if ft > sent_t), None)
                if next_t is not None and next_t > cutoff:
                    with contextlib.suppress(asyncio.TimeoutError):
                        await asyncio.wait_for(wake.wait(), timeout=max(0.001, next_t - cutoff))
                else:
                    await wake.wait()
                continue
            n += 1
            ack = d not in self.rtt or n % self.ack_every == 0  # the first write measures the link
            last_send = time.monotonic()
            try:
                await c.write_gatt_char(WRITE_CHAR, pkt(d) if callable(pkt) else pkt, response=ack)
            except Exception as e:  # noqa: BLE001 - bleak raises a mix of types
                if self.g.clients.get(d) is c:
                    self.g.clients.pop(d, None)
                    Group._warn(d, f"dropped: {e}")
                return
            if ack:
                r = time.monotonic() - last_send
                self.rtt[d] = r if d not in self.rtt else 0.7 * self.rtt[d] + 0.3 * r
            sent_t = t
            self.g.delivered[d.name] = self.g.delivered.get(d.name, 0) + 1

    async def close(self) -> None:
        self.closed = True
        for ev in self.wake.values():
            ev.set()
        for task in self.tasks.values():
            task.cancel()
        await asyncio.gather(*self.tasks.values(), return_exceptions=True)
        if self._hi_timer:
            import winbackend
            winbackend.timer_resolution(False)
            self._hi_timer = False


async def scan(seconds: float) -> None:
    print(f"Scanning {seconds:.0f}s for LED controllers...")
    found = await BleakScanner.discover(timeout=seconds, return_adv=True)
    hits = [(d, adv) for d, adv in found.values()
            if (d.name or "").upper().startswith(NAME_PREFIXES)]
    if not hits:
        print("No ELK/MELK-style devices found. Is the phone app still connected to it?")
        return
    saved = {d.addr: d.name for d in load_devices().values()}
    for d, adv in sorted(hits, key=lambda x: -x[1].rssi):
        tag = f"   saved as {saved[d.address.upper()]!r}" if d.address.upper() in saved else ""
        print(f"  {d.address}  {d.name!r}  RSSI {adv.rssi}{tag}")
    if any(d.address.upper() not in saved for d, _ in hits):
        print("\nSave a strip under a name with:  lightctl --add-device NAME MAC")


def list_devices() -> None:
    saved = load_devices()
    if not saved:
        print("No saved strips. Find them with --scan, then: lightctl --add-device NAME MAC")
        return
    print("Saved strips (commands go to all of them unless you pick some with --device):")
    for d in saved.values():
        print(f"  {d.name:<12} {d.addr}   wiring {d.order}")


async def send(devices: list[Device], packets: list[tuple[str, object]], verbose: bool) -> None:
    async with _connected(devices, verbose) as g:
        for label, pkt in packets:
            if verbose:
                if callable(pkt):
                    for d in g.live:
                        print(f"{label:<12} {pkt(d).hex(' ')}" + (f"   [{d.name}]" if len(g.live) > 1 else ""))
                else:
                    print(f"{label:<12} {pkt.hex(' ')}")
            # acknowledged write, like the app (WRITE_TYPE_DEFAULT); unacked single writes got dropped
            await g.write(pkt)
            await asyncio.sleep(0.1)


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


async def timers(devices: list[Device], set_on, set_off, days: int, verbose: bool) -> None:
    """Show both timers of every strip; set or disable them first if asked. Syncs the clock like the app."""
    async with _connected(devices, verbose) as g:
        async def one(client: BleakClient) -> list[str]:
            out: list[str] = []
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
                        out.append(f"set {label.strip():<10} {pkt.hex(' ')}")
                    await w(pkt)
                v = await _query_timer(client, which)
                if verbose and v is not None:
                    out.append(f"read {label.strip():<9} {v.hex(' ')}")
                out.append(f"{label}: {_timer_text(v)}")
            return out

        live = g.live
        results = await asyncio.gather(*(one(g.clients[d]) for d in live), return_exceptions=True)
        for d, res in zip(live, results):
            pad = "  " if len(live) > 1 else ""
            if len(live) > 1:
                print(f"[{d.name}]")
            for line in [f"error: {res}"] if isinstance(res, BaseException) else res:
                print(pad + line)


async def sync_clock(devices: list[Device], verbose: bool) -> None:
    async with _connected(devices, verbose) as g:
        async def one(client: BleakClient) -> str:
            pkt = cmd_clock(dt.datetime.now())
            if verbose:
                print(f"clock      {pkt.hex(' ')}")
            await client.write_gatt_char(WRITE_CHAR, pkt, response=True)
            v = await _read_frame(client, 0x83, 0x07)
            if v is None:
                return "clock sent (controller did not echo it back)"
            day = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"][v[6] % 7]
            return f"controller clock: {day} {v[3]:02d}:{v[4]:02d}:{v[5]:02d}"

        live = g.live
        results = await asyncio.gather(*(one(g.clients[d]) for d in live), return_exceptions=True)
        for d, res in zip(live, results):
            prefix = f"[{d.name}] " if len(live) > 1 else ""
            print(prefix + (f"error: {res}" if isinstance(res, BaseException) else res))


# ---------- music mode (what the app's Music / phone-mic tabs stream) ----------

MUSIC_PALETTE = [(255, 0, 0), (0, 255, 0), (0, 0, 255), (255, 255, 0),
                 (255, 0, 255), (0, 255, 255), (255, 255, 255)]  # Utils.musicColors, in order
AUDIO_RATE = 8000  # the app records at 8 kHz mono 16-bit


def _pactl(*args: str) -> str:  # Linux only
    try:
        return subprocess.run(["pactl", *args], capture_output=True, text=True, check=True).stdout
    except (OSError, subprocess.CalledProcessError) as e:
        raise SystemExit(f"lightctl: cannot query PulseAudio/PipeWire: {e}") from None


def audio_sources() -> list[dict]:
    """Every audio input: microphones, and 'monitor' inputs that carry what the computer is playing.
    Each is {"name": id, "desc": readable name, "monitor": bool}."""
    if IS_WINDOWS:
        import winbackend
        return winbackend.audio_sources()
    out = []
    for s in json.loads(_pactl("-f", "json", "list", "sources")):
        props = s.get("properties", {})
        monitor = props.get("device.class") == "monitor" or s["name"].endswith(".monitor")
        out.append({"name": s["name"], "desc": s.get("description") or s["name"], "monitor": monitor})
    return out


def default_audio() -> tuple[str | None, str | None]:
    """(name of the input carrying what the computer plays, name of the default microphone)."""
    if IS_WINDOWS:
        import winbackend
        return winbackend.default_audio()
    return _pactl("get-default-sink").strip() + ".monitor", _pactl("get-default-source").strip()


def choose_audio_source(source: str, sources: list[dict], default_out: str | None,
                        default_mic: str | None, preferred_mic: str | None = None) -> dict:
    """'system' | 'mic' | number (as --list-sources prints) | name | part of a name -> one source."""
    def named(name: str | None) -> dict | None:
        return next((s for s in sources if s["name"] == name), None) if name else None

    if source == "system":
        pick = named(default_out)
        if pick is None or not pick["monitor"]:
            pick = next((s for s in sources if s["monitor"]), None)
        if pick is None:
            raise SystemExit("lightctl: found no input that carries what the computer plays")
        return pick
    if source == "mic":
        if not preferred_mic:
            pick = named(default_mic)
            if pick is None or pick["monitor"]:
                pick = next((s for s in sources if not s["monitor"]), None)
            if pick is None:
                raise SystemExit("lightctl: found no microphone")
            return pick
        source = preferred_mic
    if source.isdigit():
        i = int(source)
        if not 1 <= i <= len(sources):
            raise SystemExit(f"lightctl: no audio input #{i}; see lightctl --list-sources")
        return sources[i - 1]
    exact = [s for s in sources if s["name"] == source]
    hits = exact or [s for s in sources if source.lower() in (s["name"] + " " + s["desc"]).lower()]
    if len(hits) > 1:  # a name can match a device's mic and its output monitor: prefer the mic
        mics = [s for s in hits if not s["monitor"]]
        hits = mics if len(mics) == 1 else hits
    if not hits:
        raise SystemExit(f"lightctl: no audio input matches {source!r}. Is it plugged in? "
                         "See lightctl --list-sources")
    if len(hits) > 1:
        names = "\n  ".join(f"{s['desc']}  ({s['name']})" for s in hits)
        raise SystemExit(f"lightctl: {source!r} matches several inputs, be more specific:\n  {names}")
    return hits[0]


def pick_audio_source(source: str) -> dict:
    out, mic = default_audio()
    return choose_audio_source(source, audio_sources(), out, mic, os.environ.get("LIGHTCTL_MIC"))


def list_sources() -> None:
    srcs = audio_sources()
    out, mic = default_audio()
    preferred = os.environ.get("LIGHTCTL_MIC")
    print("Audio inputs for --music (use the number, or any part of the name):")
    for i, s in enumerate(srcs, 1):
        kind = "computer audio" if s["monitor"] else "microphone"
        tags = []
        if s["monitor"] and s["name"] == out:
            tags.append("used by --music / --music system")
        if not s["monitor"] and s["name"] == mic and not preferred:
            tags.append("used by --music mic")
        print(f"  {i:>2}  {kind:<14}  {s['desc']}" + (f"   <- {'; '.join(tags)}" if tags else ""))
        print(f"      {'':<14}  {s['name']}")
    if preferred:
        print(f"\nLIGHTCTL_MIC={preferred!r} -> used by --music mic")
    setting = "set LIGHTCTL_MIC=logitech" if IS_WINDOWS else "export LIGHTCTL_MIC=logitech"
    print(f"\nexamples:  lightctl --music 2     lightctl --music webcam     {setting}")


class AudioInput:
    """Audio levels for music mode: one dB value (the app's scale) per 1/fps seconds.
    Linux: PulseAudio/PipeWire through parec. Windows: WASAPI through soundcard (winbackend.py)."""

    def __init__(self, source: str, fps: float) -> None:
        self.fps = fps
        src = pick_audio_source(source)
        self.name, self.desc, self.monitor = src["name"], src["desc"], src["monitor"]
        self.kind = "music" if self.monitor else "mic"  # computer audio or a microphone
        self.proc = None
        self.win = None

    async def __aenter__(self) -> "AudioInput":
        if IS_WINDOWS:
            import winbackend
            self.win = winbackend.WinAudioInput(self.name, self.monitor, self.fps)
            await self.win.start()
        else:
            self.proc = await asyncio.create_subprocess_exec(
                "parec", "--raw", "--format=s16le", f"--rate={AUDIO_RATE}", "--channels=1",
                f"--latency-msec={max(10, round(500 / self.fps))}", "-d", self.name,
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        return self

    async def level(self) -> float:
        if self.win is not None:
            return await self.win.level()
        try:
            buf = await self.proc.stdout.readexactly(max(1, round(AUDIO_RATE / self.fps)) * 2)
        except asyncio.IncompleteReadError:
            err = (await self.proc.stderr.read()).decode(errors="replace").strip()
            raise SystemExit(f"lightctl: audio capture from {self.name!r} stopped: "
                             f"{err or 'parec exited'}") from None
        return level_db(buf)

    async def __aexit__(self, *exc) -> None:
        if self.win is not None:
            await self.win.stop()
        if self.proc is not None and self.proc.returncode is None:
            self.proc.terminate()
            await self.proc.wait()


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


class LevelNorm:
    """dB -> 0..1 between the recent quiet level (10th percentile, 5 s) and the recent peak (2 s)."""

    def __init__(self, fps: float) -> None:
        self.lo: collections.deque[float] = collections.deque(maxlen=max(5, int(fps * 5)))
        self.hi: collections.deque[float] = collections.deque(maxlen=max(2, int(fps * 2)))

    def __call__(self, d: float) -> float:
        if d == float("-inf"):
            return 0.0
        self.lo.append(d)
        self.hi.append(d)
        lo = sorted(self.lo)[len(self.lo) // 10]
        hi = max(max(self.hi), lo + 6)
        return min(1.0, max(0.0, (d - lo) / (hi - lo)))


def resolve_effect(spec: str) -> tuple[str, int]:
    """Effect spec (name, tab:N, id, 'random') -> (label, id); 'random' is re-rolled on every call."""
    if spec.strip().lower() == "random":
        name = random.choice(list(MODES))
        return name, MODES[name][1]
    mid = parse_mode(spec)
    name = next((n for n, (_, m) in MODES.items() if m == mid), f"0x{mid:02x}")
    return name, mid


async def music_effects(devices: list[Device], source: str, fps: float, effects: list[str], switch: str | None,
                        speed: int | None, floor: int, duration: float, verbose: bool) -> None:
    """Built-in animation keeps running on the controller; the laptop pulses its brightness with the
    music and can rotate effects every N seconds or on strong beats."""
    every = None if switch in (None, "beat") else float(switch)
    norm = LevelNorm(fps)
    try:
        async with AudioInput(source, fps) as audio, _connected(devices, verbose) as g:
            async def start(i: int) -> None:
                name, mid = resolve_effect(effects[i % len(effects)])
                log(f"effect: {name} (0x{mid:02x})")
                await g.write(cmd_mode(mid))

            pos = 0
            await g.write(cmd_power(True))
            await g.write(cmd_mic(False))  # make sure the controller isn't in its own mic mode
            await start(pos)
            if speed is None and len(g.live) > 1:
                speed = SYNC_SPEED
            if speed is not None:
                await g.write(cmd_speed(speed))
            log(f"music + effect on {g.names()}, listening to {audio.desc}")
            loop = asyncio.get_running_loop()
            t0 = last_switch = loop.time()
            last_v = None
            async with LiveFeed(g) as feed:
                while not duration or loop.time() - t0 < duration:
                    d = await audio.level()
                    x = norm(d)
                    now = loop.time()
                    beat = x >= 0.9 and now - last_switch >= 1.0
                    if (every and now - last_switch >= every) or (switch == "beat" and beat):
                        pos += 1
                        last_switch = now
                        await start(pos)
                    v = round(floor + (100 - floor) * x)
                    if v != last_v:
                        feed.push(cmd_brightness(v))
                        last_v = v
                    if verbose:
                        log(f"{d:6.1f} dB  level {x:4.2f}  brightness {v:3d}")
            await g.write(cmd_brightness(100))
    except asyncio.CancelledError:
        pass


async def music(devices: list[Device], source: str, react: str | None, fps: float, palette: list,
                threshold: float | None, duration: float, verbose: bool) -> None:
    react = react or "music"
    idx, frames = 0, 0
    try:
        async with AudioInput(source, fps) as audio, _connected(devices, verbose) as g:
            # microphones never hear true silence: follow the room instead of a fixed level
            gate = AutoGate(fps) if react == "music" and audio.kind == "mic" and threshold is None else None
            limit = 40.0 if threshold is None else threshold
            mic = MicReact() if react == "mic" else None
            await g.write(cmd_power(True))
            log(f"music mode ({react} style) on {g.names()}, listening to {audio.desc} at {fps:g} frames/s")
            loop = asyncio.get_running_loop()
            t0 = loop.time()
            async with LiveFeed(g) as feed:
                while not duration or loop.time() - t0 < duration:
                    d = await audio.level()
                    if mic is not None:
                        loud = mic(d)
                        if loud is None:
                            continue
                    elif gate is not None:  # Music-tab logic on a microphone: above the room's quiet level
                        loud = gate(d)
                        if loud is None:
                            continue
                    else:  # Music-tab logic: a new colour every frame unless (near) silent
                        loud = d >= limit
                    if loud:
                        idx = (idx + 1) % len(palette)  # the app advances the palette, then uses it
                        rgb = palette[idx]
                    else:
                        rgb = (0, 0, 0)
                    feed.push(lambda dev, rgb=rgb: cmd_music(*rgb, order=dev.order))
                    frames += 1
                    if verbose:
                        bar = "#" * max(0, min(40, int((d - 20) / 2))) if d > -1e9 else ""
                        gate_txt = f" floor {gate.floor():5.1f}" if gate is not None else ""
                        log(f"{d:6.1f} dB{gate_txt} {bar:<40} {'%02x%02x%02x' % rgb}")
    except asyncio.CancelledError:
        pass
    finally:
        if verbose:
            log(f"{frames} music frames sent")


# ---------- screen sync: the strip glows with the colour of what's on screen ----------

SCREEN_W, SCREEN_H = 48, 27  # GStreamer shrinks every frame to this before Python looks at it
TOKEN_FILE = os.path.join(CONFIG_DIR, "screencast-token")  # Linux: remembered screen-share permission


class ScreenCapture:
    """Screen frames on Wayland/X11 through xdg-desktop-portal ScreenCast + PipeWire.

    The first run shows the desktop's "share your screen" picker; the permission is saved as a
    restore token so later runs start without asking. GStreamer decodes the PipeWire stream and
    scales it to SCREEN_W x SCREEN_H RGB frames on stdout.
    """

    PORTAL = "org.freedesktop.portal.Desktop"
    PATH = "/org/freedesktop/portal/desktop"
    IFACE = "org.freedesktop.portal.ScreenCast"

    def __init__(self, fps: float, verbose: bool = False, target: str = "monitor") -> None:
        self.fps, self.verbose, self.target = fps, verbose, target
        self.token_file = TOKEN_FILE if target == "monitor" else TOKEN_FILE + "-window"
        self.bus = self.session = self.proc = None

    async def _call(self, member: str, signature: str, body: list, interface: str | None = None):
        from dbus_fast import Message, MessageType
        reply = await self.bus.call(Message(destination=self.PORTAL, path=self.PATH,
                                            interface=interface or self.IFACE, member=member,
                                            signature=signature, body=body))
        if reply.message_type == MessageType.ERROR:
            raise SystemExit(f"lightctl: screen-cast portal {member} failed: {reply.error_name} {reply.body}")
        return reply

    async def _request(self, member: str, signature: str, body: list, options: dict) -> dict:
        """Portal calls answer through a Request object's Response signal."""
        from dbus_fast import Message, MessageType, Variant
        token = f"lightctl{random.randrange(1 << 30)}"
        sender = self.bus.unique_name[1:].replace(".", "_")
        path = f"/org/freedesktop/portal/desktop/request/{sender}/{token}"
        fut = asyncio.get_running_loop().create_future()

        def on_msg(msg) -> None:
            if (msg.message_type == MessageType.SIGNAL and msg.path == path
                    and msg.member == "Response" and not fut.done()):
                fut.set_result(msg.body)

        self.bus.add_message_handler(on_msg)
        await self.bus.call(Message(destination="org.freedesktop.DBus", path="/org/freedesktop/DBus",
                                    interface="org.freedesktop.DBus", member="AddMatch", signature="s",
                                    body=[f"type='signal',interface='org.freedesktop.portal.Request',"
                                          f"member='Response',path='{path}'"]))
        options = {**options, "handle_token": Variant("s", token)}
        await self._call(member, signature, [*body, options])
        try:
            code, results = await fut
        finally:
            self.bus.remove_message_handler(on_msg)
        if code != 0:
            raise SystemExit(f"lightctl: screen sharing was {'cancelled' if code == 1 else 'refused'} "
                             f"({member}, code {code})")
        return {k: v.value for k, v in results.items()}

    async def __aenter__(self) -> "ScreenCapture":
        from dbus_fast import BusType, Variant
        from dbus_fast.aio import MessageBus
        self.bus = await MessageBus(bus_type=BusType.SESSION, negotiate_unix_fd=True).connect()
        res = await self._request("CreateSession", "a{sv}", [],
                                  {"session_handle_token": Variant("s", f"lightctl{random.randrange(1 << 30)}")})
        self.session = res["session_handle"]
        opts = {"types": Variant("u", 1 if self.target == "monitor" else 2),  # a monitor / one window
                "multiple": Variant("b", False),
                "cursor_mode": Variant("u", 1),    # hidden
                "persist_mode": Variant("u", 2)}   # remember until revoked
        try:
            with open(self.token_file) as f:
                opts["restore_token"] = Variant("s", f.read().strip())
        except OSError:
            log("Your desktop will ask what to share: pick the monitor the strip is behind."
                if self.target == "monitor" else
                "Your desktop will ask what to share: pick the window playing the video.")
        await self._request("SelectSources", "oa{sv}", [self.session], opts)
        res = await self._request("Start", "osa{sv}", [self.session, ""], {})
        if res.get("restore_token"):
            os.makedirs(os.path.dirname(self.token_file), exist_ok=True)
            fd = os.open(self.token_file, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(fd, "w") as f:
                f.write(res["restore_token"])
        node = res["streams"][0][0]
        reply = await self._call("OpenPipeWireRemote", "oa{sv}", [self.session, {}])
        idx = reply.body[0]
        fd = reply.unix_fds[idx] if idx < len(reply.unix_fds) else idx
        self.proc = await asyncio.create_subprocess_exec(
            "gst-launch-1.0", "-q",
            "pipewiresrc", f"fd={fd}", f"path={node}", "always-copy=true", "do-timestamp=true", "!",
            # ask the compositor for at most fps frames (saves CPU), and enforce it with videorate
            f"video/x-raw,max-framerate={max(1, round(self.fps))}/1", "!",
            "videorate", "drop-only=true", f"max-rate={max(1, round(self.fps))}", "!",
            # multi-tap bilinear: every output pixel is the average of its whole screen area
            # (the default 2-tap filter only looks at a few pixels when shrinking this much)
            "videoscale", "method=bilinear2", "add-borders=false", "!", "videoconvert", "!",
            f"video/x-raw,format=RGB,width={SCREEN_W},height={SCREEN_H}", "!",
            "fdsink", "fd=1", "sync=false",
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE, pass_fds=(fd,))
        os.close(fd)
        return self

    async def frame(self) -> bytes:
        try:
            return await self.proc.stdout.readexactly(SCREEN_W * SCREEN_H * 3)
        except asyncio.IncompleteReadError:
            err = (await self.proc.stderr.read()).decode(errors="replace").strip()
            raise SystemExit(f"lightctl: screen capture stopped: {err or 'gst-launch exited'}") from None

    async def __aexit__(self, *exc) -> None:
        if self.proc is not None and self.proc.returncode is None:
            self.proc.terminate()
            await self.proc.wait()
        if self.session is not None:
            from dbus_fast import Message
            try:
                await self.bus.call(Message(destination=self.PORTAL, path=self.session,
                                            interface="org.freedesktop.portal.Session", member="Close"))
            except Exception:  # noqa: BLE001 - best effort on the way out
                pass
        if self.bus is not None:
            self.bus.disconnect()


_LINEAR = [(i / 255 / 12.92) if i / 255 <= 0.04045 else ((i / 255 + 0.055) / 1.055) ** 2.4 for i in range(256)]


def _srgb(x: float) -> float:
    x = min(1.0, max(0.0, x))
    return 12.92 * x if x <= 0.0031308 else 1.055 * x ** (1 / 2.4) - 0.055


def _hue(r: int, g: int, b: int, mx: int, mn: int) -> float:
    d = mx - mn
    if mx == r:
        h = ((g - b) / d) % 6
    elif mx == g:
        h = (b - r) / d + 2
    else:
        h = (r - g) / d + 4
    return h / 6


SAMPLE_MODES = {
    "dominant": "the main colour of the scene",
    "average": "the plain average of the screen",
    "vivid": "biased to bright, saturated spots (the original behaviour)",
}


class ScreenSampler:
    """Turns screen frames (SCREEN_W x SCREEN_H RGB) into one strip colour, as 0-255 floats.

    Modes:
      dominant - the hue that covers the most colourful screen area (a hue histogram, smoothed over
                 time so it doesn't flip between two similar areas), with its colour averaged from
                 those pixels; scenes that are mostly white/grey blend into their real tinted average
      average  - the plain average of the screen (what a diffuser in front of it would show)
      vivid    - every pixel weighted by brightness x colourfulness (the original behaviour)
    Colours are mixed in linear light. `led_gamma` sets how the mix is sent: 1.0 sends the true light
    ratios, right for LEDs driven by plain PWM (orange stays orange, skin stays tan); 2.2-2.4 encodes
    them like a screen does, which looks paler on LEDs (the original behaviour).
    Brightness follows the screen's average brightness x `gain`, never below `floor` %.
    """

    BINS = 24

    def __init__(self, mode: str = "dominant", region: str = "full", saturation: float = 1.3,
                 floor: int = 5, gain: float = 1.25, led_gamma: float = 1.0) -> None:
        if mode not in SAMPLE_MODES:
            raise ValueError(f"unknown sample mode {mode!r}")
        self.mode, self.saturation, self.floor, self.gain = mode, saturation, floor, gain
        self.led_gamma = max(0.1, led_gamma)
        self.hist = [0.0] * self.BINS
        w, h = SCREEN_W, SCREEN_H
        band = max(1, round(min(w, h) * 0.2))
        self.cells = [(y * w + x) * 3 for y in range(h) for x in range(w)
                      if region != "edges" or y < band or y >= h - band or x < band or x >= w - band]

    def __call__(self, frame: bytes) -> tuple[float, float, float] | None:
        bins = self.BINS
        lit, vsum = 0, 0.0
        avg = [0.0, 0.0, 0.0]
        viv = [0.0, 0.0, 0.0, 0.0]
        hist = [0.0] * bins
        colourful = []  # (bin, chroma, linear r, g, b)
        for i in self.cells:
            r, g, b = frame[i], frame[i + 1], frame[i + 2]
            mx = max(r, g, b)
            if mx == 0:
                continue
            mn = min(r, g, b)
            lit += 1
            vsum += mx / 255
            lr, lg, lb = _LINEAR[r], _LINEAR[g], _LINEAR[b]
            avg[0] += lr
            avg[1] += lg
            avg[2] += lb
            wv = (mx / 255) * (0.15 + (mx - mn) / mx)
            viv[0] += lr * wv
            viv[1] += lg * wv
            viv[2] += lb * wv
            viv[3] += wv
            chroma = (mx - mn) / 255
            if chroma >= 0.1 and mx >= 24:
                k = int(_hue(r, g, b, mx, mn) * bins) % bins
                hist[k] += chroma
                colourful.append((k, chroma, lr, lg, lb))
        if lit == 0:
            return None
        if self.mode == "vivid":
            lin = [v / viv[3] for v in viv[:3]]
        elif self.mode == "average":
            lin = [v / lit for v in avg]
        else:
            self.hist = [0.6 * o + 0.4 * x for o, x in zip(self.hist, hist)]
            smooth = [0.25 * self.hist[k - 1] + 0.5 * self.hist[k] + 0.25 * self.hist[(k + 1) % bins]
                      for k in range(bins)]
            peak = max(range(bins), key=smooth.__getitem__)
            sel = [c for c in colourful if min((c[0] - peak) % bins, (peak - c[0]) % bins) <= 1]
            base = [v / lit for v in avg]
            if sel:
                wsum = sum(c[1] for c in sel)
                dom = [sum(c[1] * c[2 + j] for c in sel) / wsum for j in range(3)]
                share = len(colourful) / lit  # how much of the lit screen is colourful at all
                t = min(1.0, max(0.0, (share - 0.08) / 0.17))
                lin = [a + (d - a) * t for a, d in zip(base, dom)]
            else:
                lin = base
        top = max(lin) or 1.0
        ratios = [(v / top) ** (1 / self.led_gamma) for v in lin]
        hue, sat, _ = colorsys.rgb_to_hsv(*ratios)
        level = min(1.0, vsum / len(self.cells) * self.gain)
        value = (self.floor + (100 - self.floor) * level) / 100
        r, g, b = colorsys.hsv_to_rgb(hue, min(1.0, sat * self.saturation), value)
        return r * 255, g * 255, b * 255


def screen_color(frame: bytes, region: str = "full", saturation: float = 1.3, floor: int = 5,
                 gain: float = 1.25, mode: str = "dominant", led_gamma: float = 1.0):
    """One-off colour of a single frame (see ScreenSampler)."""
    return ScreenSampler(mode, region, saturation, floor, gain, led_gamma)(frame)


def open_screen_capture(fps: float, verbose: bool, capture: str):
    """capture: 'monitor', 'monitor:N', 'window' or 'window:TITLE'. On Linux the desktop's dialog
    picks the monitor/window (N and TITLE are Windows-only); on Windows mss grabs it directly."""
    if IS_WINDOWS:
        import winbackend
        return winbackend.WinScreenCapture(fps, verbose, capture, SCREEN_W, SCREEN_H, log=log)
    return ScreenCapture(fps, verbose, capture.partition(":")[0])


async def screen_sync(devices: list[Device], fps: float, smooth: float, region: str, saturation: float,
                      floor: int, duration: float, verbose: bool, gain: float = 1.25,
                      mode: str = "dominant", led_gamma: float = 1.0, capture: str = "monitor") -> None:
    """Capture -> one colour per frame -> smooth fade at `fps` -> unacknowledged colour frames."""
    target: list[float] | None = None
    sampler = ScreenSampler(mode, region, saturation, floor, gain, led_gamma)
    # capturing faster than this costs CPU (and laptop heat) without making the light smoother:
    # the link delivers ~10-16 updates/s and the fade fills in between. Grabbing on Windows (mss + Pillow)
    # costs more per frame than PipeWire on Linux, so it is capped a little lower there.
    cap_fps = min(fps, 15 if IS_WINDOWS else 20)
    async with open_screen_capture(cap_fps, verbose, capture) as cap, _connected(devices, verbose) as g:
        await g.write(cmd_power(True))
        await g.write(cmd_brightness(100, 0x01))
        log(f"screen sync on {g.names()}: {SAMPLE_MODES[mode]}, up to {fps:g} updates/s, fade {smooth:g}s")

        async def reader() -> None:
            nonlocal target
            while True:
                c = sampler(await cap.frame())
                if c is not None:
                    target = list(c)
                elif target is not None:  # black screen: keep the hue, drop to the floor
                    peak = max(target) or 1.0
                    target = [v / peak * 255 * floor / 100 for v in target]

        task = asyncio.create_task(reader())
        loop = asyncio.get_running_loop()
        t0 = last_t = loop.time()
        rate = max(fps, 25)  # colours are worked out often; each strip takes the newest when it can
        cur: list[float] | None = None
        last: tuple[int, int, int] | None = None
        frames = 0
        try:
            async with LiveFeed(g) as feed:
                while not duration or loop.time() - t0 < duration:
                    tick = loop.time()
                    if task.done():
                        task.result()  # re-raise capture errors
                    # fade by the real time since the last step
                    alpha = 1 - math.exp(-(tick - last_t) / smooth) if smooth > 0 else 1.0
                    last_t = tick
                    if target is not None:
                        cur = list(target) if cur is None else [c + (t - c) * alpha for c, t in zip(cur, target)]
                        out = tuple(max(0, min(255, round(v))) for v in cur)
                        if out != last:
                            feed.push(lambda d, out=out: cmd_color(*out, order=d.order))
                            last = out
                            frames += 1
                            if verbose:
                                log(f"{'%02x%02x%02x' % out}")
                    await asyncio.sleep(max(0.0, 1 / rate - (loop.time() - tick)))
        except asyncio.CancelledError:
            pass
        finally:
            task.cancel()
            if verbose:
                log(f"{frames} colours worked out; per strip sent: {g.delivered}")


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


async def animate(devices: list[Device], name: str, duration: float, period: float | None,
                  color: tuple[int, int, int] | None, fps: float, verbose: bool,
                  fast: bool = False) -> None:
    fn, default_period, default_color = ANIMATIONS[name]
    period = period or default_period
    base = color or default_color or (255, 255, 255)
    first = _scale(base, 0.0) if name == "breathe" else base
    async with _connected(devices, verbose) as g:
        await g.write(cmd_power(True))
        await g.write(lambda d: cmd_color(*first, order=d.order))
        await g.write(cmd_brightness(100, 0x01))  # also drops out of any built-in effect
        loop = asyncio.get_running_loop()
        t0 = loop.time()
        frames, last = 0, None
        log(f"animating {name!r} (period {period}s)")
        try:
            async with LiveFeed(g, ack_every=8 if fast else 1) as feed:
                while True:
                    now = loop.time() - t0
                    if duration and now >= duration:
                        break
                    rgb = fn(now, period, base)
                    if rgb != last:
                        feed.push(lambda d, rgb=rgb: cmd_color(*rgb, order=d.order))
                        last = rgb
                        frames += 1
                    await asyncio.sleep(max(0.0, 1 / fps - (loop.time() - t0 - now)))
        except asyncio.CancelledError:
            pass
        finally:
            el = loop.time() - t0
            if verbose and el > 0:
                log(f"{frames} frames in {el:.1f}s; per strip sent: "
                    + ", ".join(f"{k} {v / el:.1f}/s" for k, v in g.delivered.items()))


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
               "  lightctl --music --effect random --switch beat   # animations pulsing with the music\n"
               "  lightctl --screen                           # glow with the colour of the screen\n"
               "  lightctl --animate rainbow\n"
               "  lightctl --timer-on 07:30 --days weekdays --timer-off 23:00\n"
               "  lightctl --add-device room BE:69:04:14:32:04   # save a second strip\n"
               "  lightctl -D room --color blue               # only that strip (default: all saved)\n"
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
    e.add_argument("--speed", "-s", type=parse_pct, metavar="0-100",
                   help=f"effect speed (app default 100; with several strips {SYNC_SPEED} unless given)")
    e.add_argument("--list-modes", nargs="?", const="", metavar="FILTER",
                   help="list effects, optionally filtered by word or tab")
    e.add_argument("--tour", nargs="?", const="", metavar="FILTER",
                   help="play effects one by one (all, a tab like 'water', or a word like 'strobe')")
    e.add_argument("--interval", "-i", type=float, default=8, help="seconds per effect in --tour (default 8)")

    m = p.add_argument_group("sound")
    m.add_argument("--music", nargs="?", const="system", metavar="SOURCE",
                   help="stream the app's music animation from computer audio: 'system' (what is playing, "
                        "default), 'mic' (default mic, or $LIGHTCTL_MIC), or an input number / name / "
                        "part of a name from --list-sources (e.g. 'webcam', 'logitech')")
    m.add_argument("--list-sources", action="store_true", help="list microphones and other audio inputs")
    m.add_argument("--effect", nargs="+", metavar="MODE",
                   help="--music with animation: run built-in effect(s) and pulse their brightness with the "
                        "music (names, tab:N, ids or 'random')")
    m.add_argument("--switch", metavar="SECONDS|beat",
                   help="with --effect: move to the next effect every N seconds, or on strong beats")
    m.add_argument("--floor", type=parse_pct, default=5, metavar="0-100",
                   help="lowest brightness: between beats with --effect, in dark scenes with --screen (default 5)")
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

    sc = p.add_argument_group("screen sync (the strip glows with what's on screen)")
    sc.add_argument("--screen", action="store_true",
                    help="match the strip to the screen's colour (first run asks which monitor to share)")
    sc.add_argument("--smooth", type=float, default=0.12, metavar="SECONDS",
                    help="fade time constant between colours (default 0.12; 0 = instant)")
    sc.add_argument("--saturation", type=float, default=1.3,
                    help="colour boost, 1.0 = as on screen, up to ~4 for very vivid (default 1.3)")
    sc.add_argument("--gain", type=float, default=1.25,
                    help="brightness boost, 1.0 = as bright as the screen (default 1.25)")
    sc.add_argument("--region", choices=["full", "edges"], default="full",
                    help="sample the whole screen (default) or only its outer edges")
    sc.add_argument("--sample", choices=list(SAMPLE_MODES), default="dominant",
                    help="how the colour is picked: dominant = the scene's main colour (default), "
                         "average = plain average, vivid = bright saturated spots (original)")
    sc.add_argument("--led-gamma", type=float, default=1.0,
                    help="1.0 = send true light mixes, right for PWM LEDs (default); 2.2 = paler, "
                         "screen-like (original)")
    sc.add_argument("--capture", type=parse_capture, default="monitor", metavar="TARGET",
                    help="monitor (default), window = only one window e.g. the video player. Windows also: "
                         "monitor:N, window:TITLE (see --list-windows); on Linux the desktop asks in a dialog")
    sc.add_argument("--list-windows", action="store_true", help="Windows: list monitors and windows to capture")
    sc.add_argument("--screen-reset", action="store_true",
                    help="forget the shared-monitor choice so the next --screen asks again")

    a_ = p.add_argument_group("laptop-driven animation")
    a_.add_argument("--animate", "-a", choices=list(ANIMATIONS),
                    help="stream an animation; --color sets the base colour for breathe")
    a_.add_argument("--duration", "-d", type=float, default=0,
                    help="seconds to run --animate / --music / --screen / --tour (default: until Ctrl-C)")
    a_.add_argument("--period", type=float, help="seconds per animation cycle")
    a_.add_argument("--fps", type=float,
                    help="target updates per second (default: 20 for --animate, 12 for --screen, 10 for --music "
                         "like the app). Streams never go faster than the Bluetooth link delivers (~13/s here)")
    a_.add_argument("--fast", action="store_true",
                    help="--animate: pace writes by the link instead of acknowledging each (more updates/s)")

    s = p.add_argument_group("settings stored on the controller")
    s.add_argument("--set-wiring", type=parse_order, metavar="ORDER",
                   help="store a strip's real LED order on its controller so colours and built-in effects come "
                        "out right (then it uses --order rgb, saved for you). Find the order after "
                        "'--set-wiring rgb' (= no re-mapping). One strip at a time (-D NAME)")
    s.add_argument("--leds", type=parse_leds, metavar="N",
                   help="number of addressable points on the strip (10-1000)")
    s.add_argument("--sync-clock", action="store_true", help="set the controller clock to the laptop's")
    s.add_argument("--timer-on", type=parse_timer, metavar="HH:MM|off", help="daily switch-on time")
    s.add_argument("--timer-off", type=parse_timer, metavar="HH:MM|off", help="daily switch-off time")
    s.add_argument("--days", type=parse_days, default=0x7F,
                   help="days for --timer-on/--timer-off: daily (default), weekdays, weekends, mon,wed,fri, mon-fri")
    s.add_argument("--timers", action="store_true", help="show the timers stored on the controller")

    c = p.add_argument_group("strips, connection & debugging")
    c.add_argument("--device", "-D", "--addr", dest="targets", metavar="NAMES",
                   help="which strips: saved names and/or MACs, comma-separated, or 'all' "
                        "(default: every saved strip; env LIGHTCTL_ADDR)")
    c.add_argument("--devices", action="store_true", help="list saved strips")
    c.add_argument("--add-device", nargs=2, metavar=("NAME", "MAC"),
                   help="save a strip under a name (add --order to record its wiring; default rgb)")
    c.add_argument("--remove-device", metavar="NAME", help="forget a saved strip")
    c.add_argument("--order", type=parse_order,
                   help="colour wiring of the chosen strips, overriding what is saved (rgb, gbr, ...)")
    c.add_argument("--scan", action="store_true", help="find nearby LED controllers")
    c.add_argument("--raw", type=parse_raw, action="append", metavar="HEX",
                   help="send a raw 9-byte payload (repeatable)")
    c.add_argument("-v", "--verbose", action="store_true", help="print bytes sent / audio levels")
    c.add_argument("--ui", action="store_true", help="open the interactive terminal UI (same as no arguments)")
    if IS_WINDOWS:  # never crash on a character the console can't show
        for stream in (sys.stdout, sys.stderr):
            with contextlib.suppress(AttributeError, ValueError):
                stream.reconfigure(errors="replace")
    if len(sys.argv) == 1 and sys.stdin.isatty() and sys.stdout.isatty():
        sys.argv.append("--ui")
    a = p.parse_args()
    if a.ui:
        try:
            from lightui import run_ui
        except ImportError as e:
            pip_cmd = r".venv\Scripts\pip" if IS_WINDOWS else ".venv/bin/pip"
            raise SystemExit(f"lightctl: the UI needs Textual ({e}); run: {pip_cmd} install -r requirements.txt")
        run_ui(a.targets, a.order)
        return

    def run(coro, stopped_msg: str = "stopped") -> None:
        if a.animate or a.music or a.screen or a.tour is not None:
            log("(Ctrl-C to stop)")
        try:
            asyncio.run(coro)
        except KeyboardInterrupt:
            log(stopped_msg)

    if a.list_modes is not None:
        list_modes(a.list_modes or None)
        return
    if a.devices:
        list_devices()
        return
    if a.add_device:
        name, mac = a.add_device
        if not re.fullmatch(r"[A-Za-z0-9_-]+", name) or name.lower() == "all" or MAC_RE.fullmatch(name):
            p.error("--add-device: NAME may use letters, digits, - and _ (not 'all' or a MAC)")
        if not MAC_RE.fullmatch(mac):
            p.error(f"--add-device: {mac!r} is not a MAC address like BE:69:04:14:32:04")
        saved = {k: v for k, v in load_devices().items() if v.addr != mac.upper() and k != name}
        saved[name] = Device(name, mac.upper(), a.order or os.environ.get("LIGHTCTL_ORDER", DEFAULT_ORDER))
        save_devices(saved)
        print(f"saved {name!r} = {mac.upper()} (wiring {saved[name].order})")
        list_devices()
        return
    if a.remove_device:
        saved = load_devices()
        if a.remove_device not in saved:
            p.error(f"--remove-device: no saved strip called {a.remove_device!r}")
        del saved[a.remove_device]
        save_devices(saved)
        print(f"forgot {a.remove_device!r}")
        list_devices()
        return
    if a.list_sources:
        list_sources()
        return
    if a.list_windows:
        list_windows()
        return
    if a.scan:
        run(scan(8))
        return
    devices = resolve_targets(a.targets, a.order)
    if a.set_wiring:
        if len(devices) != 1:
            p.error("--set-wiring: pick one strip with -D NAME (each strip has its own wiring)")
        # once the controller knows the wiring it re-maps by itself: plain R,G,B from now on
        devices = [dataclasses.replace(devices[0], order="rgb")]
    if a.animate:
        run(animate(devices, a.animate, a.duration, a.period, a.color,
                    a.fps or 20, a.verbose, a.fast))
        return
    if a.screen_reset:
        gone = []
        for path in (TOKEN_FILE, TOKEN_FILE + "-window"):
            with contextlib.suppress(FileNotFoundError):
                os.remove(path)
                gone.append(path)
        print("forgot what to share; the next --screen will ask again" if gone else "nothing to forget")
        if not a.screen:
            return
    if a.screen:
        if a.smooth < 0 or a.saturation < 0 or a.gain <= 0 or a.led_gamma <= 0:
            p.error("--smooth/--saturation must not be negative, --gain/--led-gamma must be positive")
        run(screen_sync(devices, a.fps or 12, a.smooth, a.region, a.saturation, a.floor,
                        a.duration, a.verbose, a.gain, a.sample, a.led_gamma, a.capture))
        return
    if a.music and a.effect:
        for spec in a.effect:  # validate names before connecting
            if spec.strip().lower() != "random":
                try:
                    parse_mode(spec)
                except argparse.ArgumentTypeError as err:
                    p.error(f"--effect: {err}")
        if a.switch not in (None, "beat"):
            try:
                float(a.switch)
            except ValueError:
                p.error("--switch must be a number of seconds or 'beat'")
        run(music_effects(devices, a.music, a.fps or 10, a.effect, a.switch, a.speed, a.floor,
                          a.duration, a.verbose))
        return
    if a.music:
        run(music(devices, a.music, a.react, a.fps or 10, a.palette or MUSIC_PALETTE,
                  a.threshold, a.duration, a.verbose))
        return
    if a.tour is not None:
        run(tour(devices, a.tour or None, a.interval, a.speed, a.duration, a.verbose),
            "stopped - the last effect listed keeps running")
        return
    if a.timers or a.timer_on is not None or a.timer_off is not None:
        run(timers(devices, a.timer_on, a.timer_off, a.days, a.verbose))
        return
    if a.sync_clock:
        run(sync_clock(devices, a.verbose))
        return

    packets: list[tuple[str, object]] = []
    if a.power == "on":
        packets.append(("power on", cmd_power(True)))
    if a.set_wiring:
        packets.append(("wiring", cmd_wiring(a.set_wiring)))
    if a.leds is not None:
        packets.append(("leds", cmd_leds(a.leds)))
    if a.color is not None:
        packets.append(("color", lambda d, c=a.color: cmd_color(*c, order=d.order)))
    if a.mode is not None:
        packets.append(("mode", cmd_mode(a.mode)))
        if a.speed is None and len(devices) > 1:  # several strips: same speed so they move alike
            packets.append(("speed", cmd_speed(SYNC_SPEED)))
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
    run(send(devices, packets, a.verbose))
    if a.set_wiring:
        d, saved = devices[0], load_devices()
        if d.name in saved:
            saved[d.name] = dataclasses.replace(saved[d.name], order=d.order)
            save_devices(saved)
        log(f"{d.name}: wiring {a.set_wiring} stored on the controller; colours are now sent as plain rgb"
            + ("" if d.name in saved else " (use --order rgb for this strip)"), err=False)


if __name__ == "__main__":
    main()

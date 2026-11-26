#!/home/subhasish/projects/strip-light-cli/.venv/bin/python
"""lightctl - control a MELK / ELK-BLEDOM Bluetooth LED strip from Linux.

Protocol taken from the Magic Lantern app (wl.smartled.rgb 6.11.13,
com.easylink.colorful.service.BluetoothLEService) and verified on a MELK-OA10.
GATT service fff0, write characteristic fff3. Every command is 9 bytes:

    7e <len> <cmd> ...payload... ef

    power on/off   7e 04 04 01 00 01 ff 00 ef  /  7e 04 04 00 00 00 ff 00 ef
    colour         7e 07 05 03 RR GG BB 10 ef
    brightness     7e 04 01 BB LM ff ff 00 ef      BB 0-100, LM light mode (ff = any)
    effect         7e 05 03 MM 06 ff ff 00 ef      MM from MODES below
    effect speed   7e 04 02 SS ff ff ff 00 ef      SS 0-100
"""
from __future__ import annotations

import argparse
import asyncio
import colorsys
import math
import os
import random
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


def cmd_color(r: int, g: int, b: int, order: str = "rgb") -> bytes:
    """order = which colour each of the 3 payload bytes drives on the strip's wiring."""
    ch = {"r": r, "g": g, "b": b}
    x, y, z = (ch[c] for c in order)
    return bytes([0x7E, 0x07, 0x05, 0x03, x, y, z, 0x10, 0xEF])


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


def parse_mode(s: str) -> int:
    key = s.strip().lower()
    if key in MODES:
        return MODES[key][1]
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


def parse_raw(s: str) -> bytes:
    try:
        return bytes.fromhex(s.replace(":", " "))
    except ValueError:
        raise argparse.ArgumentTypeError("raw payload must be hex, e.g. '7e 04 04 01 00 01 ff 00 ef'") from None


def list_modes(filter_: str | None) -> None:
    current = None
    for name, (group, mid) in MODES.items():
        if filter_ and filter_.lower() not in name and filter_.lower() != group.lower():
            continue
        if group != current:
            print(f"\n{group}:")
            current = group
        print(f"  {name:<34} 0x{mid:02x}")


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
        description="Control a MELK / ELK-BLEDOM Bluetooth LED strip.",
        epilog="examples:\n"
               "  lightctl --power on --color red --brightness 60\n"
               "  lightctl --color 255,120,0\n"
               "  lightctl --mode 7-color-race --speed 50\n"
               "  lightctl --list-modes water\n"
               "  lightctl --power off",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("--power", choices=["on", "off"])
    p.add_argument("--color", "--colour", type=parse_color, metavar="COLOR",
                   help="name, hex (ff8800 / #f80) or R,G,B")
    p.add_argument("--brightness", "-b", type=parse_pct, metavar="0-100")
    p.add_argument("--mode", "-m", type=parse_mode, metavar="MODE",
                   help="effect name or id, e.g. 7-color-jump or 0xc1 (see --list-modes)")
    p.add_argument("--speed", "-s", type=parse_pct, metavar="0-100", help="effect speed")
    p.add_argument("--raw", type=parse_raw, action="append", metavar="HEX",
                   help="send a raw 9-byte payload (repeatable) - for experimenting")
    p.add_argument("--addr", default=os.environ.get("LIGHTCTL_ADDR", DEFAULT_ADDR),
                   help=f"device MAC (env LIGHTCTL_ADDR, default {DEFAULT_ADDR})")
    p.add_argument("--order", type=parse_order,
                   default=parse_order(os.environ.get("LIGHTCTL_ORDER", DEFAULT_ORDER)),
                   help=f"strip channel wiring (env LIGHTCTL_ORDER, default {DEFAULT_ORDER})")
    p.add_argument("--scan", action="store_true", help="find nearby LED controllers")
    p.add_argument("--list-modes", nargs="?", const="", metavar="FILTER",
                   help="list effects, optionally filtered by word or group")
    g = p.add_argument_group("host-driven animation (laptop stays connected)")
    g.add_argument("--animate", "-a", choices=list(ANIMATIONS),
                   help="stream an animation; --color sets the base colour for breathe")
    g.add_argument("--duration", "-d", type=float, default=0,
                   help="seconds to run (default: until Ctrl-C)")
    g.add_argument("--period", type=float, help="seconds per cycle (animation-specific default)")
    g.add_argument("--fps", type=float, default=20, help="max updates per second (default 20)")
    g.add_argument("--fast", action="store_true",
                   help="don't wait for acks on animation frames (smoother, may drop a frame)")
    p.add_argument("-v", "--verbose", action="store_true", help="print bytes sent")
    a = p.parse_args()

    if a.animate:
        try:
            asyncio.run(animate(a.addr, a.animate, a.duration, a.period, a.color,
                                a.order, a.fps, a.verbose, a.fast))
        except KeyboardInterrupt:
            print("stopped", file=sys.stderr)
        return

    if a.list_modes is not None:
        list_modes(a.list_modes or None)
        return
    if a.scan:
        asyncio.run(scan(8))
        return

    packets: list[tuple[str, bytes]] = []
    if a.power == "on":
        packets.append(("power on", cmd_power(True)))
    if a.color is not None:
        packets.append(("color", cmd_color(*a.color, order=a.order)))
    if a.mode is not None:
        packets.append(("mode", cmd_mode(a.mode)))
    if a.speed is not None:
        packets.append(("speed", cmd_speed(a.speed)))
    if a.brightness is not None:
        lm = 0x01 if a.color is not None and a.mode is None else 0xFF
        packets.append(("brightness", cmd_brightness(a.brightness, lm)))
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

#!/usr/bin/env python3
"""Can the strips' Bluetooth link go faster?  (diagnostic - needs root for raw HCI access)

    sudo ~/projects/strip-light-cli/.venv/bin/python ~/projects/strip-light-cli/tools/linkspeed.py

1. connects to the saved strips and reports the connection interval each one negotiated,
2. measures how many writes per second get through,
3. asks the controller for a 7.5-15 ms interval (HCI LE Connection Update),
4. watches whether the strip accepts it or asks to go back, and measures again.

Nothing is stored: the faster interval only lasts for this connection.
"""
import asyncio
import fcntl
import os
import socket
import struct
import sys
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import lightctl as L  # noqa: E402

HCI_DEV = 0
HCIGETCONNLIST = 0x800448D4  # _IOR('H', 212, int)
FAST_MIN, FAST_MAX = 6, 12   # x 1.25 ms = 7.5 - 15 ms


class Hci:
    """Raw HCI socket: reads LE connection events, sends LE Connection Update."""

    def __init__(self) -> None:
        self.sock = socket.socket(socket.AF_BLUETOOTH, socket.SOCK_RAW, socket.BTPROTO_HCI)
        self.sock.bind((HCI_DEV,))
        events = (1 << 0x0E) | (1 << 0x0F)                   # command complete / status
        self.sock.setsockopt(socket.SOL_HCI, socket.HCI_FILTER,
                             struct.pack("<IIIH2x", 1 << 0x04, events, 1 << (0x3E - 32), 0))
        self.log: list[str] = []
        self.interval: dict[int, float] = {}                 # handle -> current interval in ms
        threading.Thread(target=self._read, daemon=True).start()

    def _read(self) -> None:
        while True:
            d = self.sock.recv(300)
            if len(d) < 4 or d[0] != 0x04:
                continue
            if d[1] == 0x0F:                                 # command status
                status, _, opcode = struct.unpack_from("<BBH", d, 3)
                if opcode == 0x2013:
                    self.log.append(f"controller: connection update command status 0x{status:02x}")
                continue
            if d[1] != 0x3E:
                continue
            sub = d[3]
            if sub == 0x01:                                  # LE Connection Complete
                status, handle = struct.unpack_from("<BH", d, 4)
                iv, lat, to = struct.unpack_from("<HHH", d, 15)
                self._set(handle, iv, f"connected (status {status})", lat, to)
            elif sub == 0x0A:                                # LE Enhanced Connection Complete
                status, handle = struct.unpack_from("<BH", d, 4)
                iv, lat, to = struct.unpack_from("<HHH", d, 27)
                self._set(handle, iv, f"connected (status {status})", lat, to)
            elif sub == 0x03:                                # LE Connection Update Complete
                status, handle, iv, lat, to = struct.unpack_from("<BHHHH", d, 4)
                self._set(handle, iv, f"interval changed (status {status})", lat, to)
            elif sub == 0x06:                                # strip asks for other parameters
                handle, mn, mx, lat, to = struct.unpack_from("<HHHHH", d, 4)
                self.log.append(f"handle {handle}: strip ASKS for {mn * 1.25:.1f}-{mx * 1.25:.1f} ms "
                                f"(latency {lat}, timeout {to * 10} ms)")

    def _set(self, handle: int, iv: int, what: str, lat: int, to: int) -> None:
        self.interval[handle] = iv * 1.25
        self.log.append(f"handle {handle}: {what}: interval {iv * 1.25:.2f} ms, latency {lat}, "
                        f"timeout {to * 10} ms")

    def handles(self) -> dict[str, int]:
        n = 16
        res = fcntl.ioctl(self.sock.fileno(), HCIGETCONNLIST, struct.pack("<HH", HCI_DEV, n) + bytes(16 * n))
        out = {}
        for i in range(struct.unpack_from("<HH", res, 0)[1]):
            handle, bdaddr, ltype, *_ = struct.unpack_from("<H6sBBHI", res, 4 + 16 * i)
            if ltype == 0x80:                                # LE link
                out[":".join(f"{b:02X}" for b in bdaddr[::-1])] = handle
        return out

    def update(self, handle: int) -> None:
        p = struct.pack("<HHHHHHH", handle, FAST_MIN, FAST_MAX, 0, 200, 0, 0)
        self.sock.send(struct.pack("<BHB", 0x01, 0x2013, len(p)) + p)

    def flush(self, title: str) -> None:
        print(f"\n== {title}")
        for line in self.log:
            print("   " + line)
        self.log.clear()


async def burst(g: L.Group, d: L.Device, n: int = 40) -> float:
    c = g.clients[d]
    t = time.perf_counter()
    for i in range(n):
        await c.write_gatt_char(L.WRITE_CHAR, L.cmd_color(30 + i % 2, 30, 30, order=d.order), response=False)
    await c.write_gatt_char(L.WRITE_CHAR, L.cmd_timer_query(L.TIMER_ON), response=True)
    return (n + 1) / (time.perf_counter() - t)


async def main() -> None:
    if os.geteuid() != 0:
        raise SystemExit("run with sudo (raw Bluetooth controller access needs root)")
    if os.environ.get("SUDO_USER"):  # use the invoking user's saved strips, not root's
        L.DEVICES_FILE = os.path.join(os.path.expanduser(f"~{os.environ['SUDO_USER']}"),
                                      ".config", "lightctl", "devices.json")
    hci = Hci()
    devices = L.resolve_targets(None)
    async with L.Group(devices) as g:
        await asyncio.sleep(3)  # let any parameter negotiation by the strips happen
        handles = hci.handles()
        hci.flush("connection set-up")
        for d in g.live:
            h = handles.get(d.addr)
            iv = hci.interval.get(h)
            print(f"   {d.name}: handle {h}, interval {f'{iv:.2f} ms' if iv else 'unknown (connected before?)'}")
        before = {d.name: [await burst(g, d) for _ in range(2)] for d in g.live}
        print("\n== throughput now")
        for name, r in before.items():
            print(f"   {name}: {r[0]:.1f} / {r[1]:.1f} writes per second")

        for d in g.live:
            if d.addr in handles:
                hci.update(handles[d.addr])
        await asyncio.sleep(6)  # time to switch, and to see whether a strip asks to go back
        hci.flush(f"after asking for {FAST_MIN * 1.25}-{FAST_MAX * 1.25} ms")

        after = {d.name: [await burst(g, d) for _ in range(2)] for d in g.live}
        await asyncio.sleep(1)
        hci.flush("during the second measurement")
        print("\n== throughput with the faster interval")
        for name, r in after.items():
            print(f"   {name}: {r[0]:.1f} / {r[1]:.1f} writes per second   (before: {before[name][0]:.1f} / "
                  f"{before[name][1]:.1f})")
        await g.write(lambda d: L.cmd_color(255, 140, 40, order=d.order))  # leave a warm colour on


if __name__ == "__main__":
    asyncio.run(main())

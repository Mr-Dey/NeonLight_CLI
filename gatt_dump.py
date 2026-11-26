#!/usr/bin/env python3
"""Connect to the strip and print every GATT service/characteristic."""
import asyncio
import sys

from bleak import BleakClient

ADDR = sys.argv[1] if len(sys.argv) > 1 else "BE:69:32:13:B6:00"


async def main() -> None:
    async with BleakClient(ADDR, timeout=20) as client:
        print(f"connected: {client.is_connected}")
        for svc in client.services:
            print(f"[service] {svc.uuid}  {svc.description}")
            for ch in svc.characteristics:
                props = ",".join(ch.properties)
                print(f"   [char] {ch.uuid}  handle=0x{ch.handle:04x}  ({props})  {ch.description}")
                if "read" in ch.properties:
                    try:
                        val = await client.read_gatt_char(ch)
                        print(f"          value: {val.hex(' ')}  {val!r}")
                    except Exception as e:  # noqa: BLE001
                        print(f"          read failed: {e}")


asyncio.run(main())

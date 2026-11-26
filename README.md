# NeonLight CLI

Control a Bluetooth LED / neon strip (MELK / ELK-BLEDOM family, normally driven by
the **Magic Lantern** Android app) straight from a Linux terminal.

Tested on a `MELK-OA10` controller from Garuda Linux (BlueZ) with Python 3.14 and
[bleak](https://github.com/hbldh/bleak).

## Features

- Power on/off, RGB colour (name, hex or `R,G,B`), brightness
- All 213 built-in controller effects (chase, water, flow, tail, curtain, …) with speed
- Host-driven animations streamed from the laptop: `rainbow`, `breathe`, `fire`, `police`
- Raw-payload mode for experimenting, BLE scan for finding controllers

## Install

```bash
git clone git@github.com:Mr-Dey/NeonLight_CLI.git ~/projects/strip-light-cli
cd ~/projects/strip-light-cli
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
chmod +x lightctl.py
ln -sf "$PWD/lightctl.py" ~/.local/bin/lightctl
```

The shebang in `lightctl.py` points at `~/projects/strip-light-cli/.venv/bin/python`.
If you clone somewhere else, edit that first line to your venv's Python.

Find your strip and set its address (or edit `DEFAULT_ADDR` in the script):

```bash
lightctl --scan
export LIGHTCTL_ADDR=AA:BB:CC:DD:EE:FF
```

> Close the phone app first. The controller accepts only one BLE connection.

## Usage

```bash
lightctl --power on --color red --brightness 60
lightctl --color 255,120,0                    # or ff7800, #f70, warm, purple…
lightctl --mode 7-color-race --speed 70       # built-in effect by name
lightctl --mode run:12                        # or by app tab + number, as numbered in the app
lightctl --mode random
lightctl --list-modes water                   # search effects by word or tab
lightctl --tour                               # play all 213 effects one by one, printing names
lightctl --tour flow --interval 5 --speed 80  # tour one tab (or a word like "rainbow")
lightctl --animate rainbow                    # streams until Ctrl-C
lightctl --animate breathe --color purple --period 5
lightctl --animate fire --fast --duration 30
lightctl --power off
```

`--fast` sends animation frames without waiting for BLE acknowledgements
(about 20 updates/s instead of about 6). A dropped frame is simply replaced by the next one.

## Protocol

GATT service `fff0`, write characteristic `fff3`. Each command is 9 bytes,
`7e <len> <cmd> … ef`, written **with response**. Unacknowledged writes were
silently dropped by the controller.

| Action        | Frame                                  | Notes                                   |
|---------------|----------------------------------------|-----------------------------------------|
| Power on/off  | `7e 04 04 01 00 01 ff 00 ef` / `7e 04 04 00 00 00 ff 00 ef` | |
| Colour        | `7e 07 05 03 X Y Z 10 ef`              | byte order depends on strip wiring      |
| Brightness    | `7e 04 01 BB LM ff ff 00 ef`           | BB 0–100; LM `01` = RGB mode, `ff` = keep |
| Effect        | `7e 05 03 MM 06 ff ff 00 ef`           | MM = effect id (`--list-modes`)         |
| Effect speed  | `7e 04 02 SS ff ff ff 00 ef`           | SS 0–100                                |

### Effect coverage

The 213 effects are every entry in the app's Mode tab (Basic, Curtain, Trans, Water,
Flow, Tail, Run, RunBack), using the app's own ids. The app's 28 "Scenes"
(`7e 05 31 <id> 07 ff ff 01 ef`) are only enabled for MELK-OC/OT controllers and one
OA10 firmware. They had no effect on the tested strip, so they are left out.

### Channel order

Strips are wired differently. On the tested strip, the colour payload bytes drive
**green, blue, red** (`--order gbr`, the default). If colours come out wrong, try
the other orders:

```bash
lightctl --order rgb --color red      # or grb, brg, bgr, rbg
export LIGHTCTL_ORDER=rgb             # make it permanent
```

## How it was figured out

1. `gatt_dump.py` lists the controller's GATT services and characteristics.
2. Generic ELK-BLEDOM frames (`7e 00 …`) handled colour but not effects.
3. Decompiling the Magic Lantern APK (`wl.smartled.rgb` 6.11.13) with jadx showed the
   exact frames in `BluetoothLEService` and the effect ids in the app's resources.

## Disclaimer

Not affiliated with the Magic Lantern app or the controller's manufacturer.
Interoperability project for personal use. No app code is included.

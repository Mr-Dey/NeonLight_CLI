# NeonLight CLI

Control a Bluetooth LED / neon strip (MELK / ELK-BLEDOM family, normally driven by
the **Magic Lantern** Android app) straight from a Linux terminal. Everything the app
can do with a `MELK-OA10` controller is available, plus a few extras.

Tested on a `MELK-OA10` controller from Garuda Linux (BlueZ + PipeWire) with Python 3.14
and [bleak](https://github.com/hbldh/bleak).

## Features

- Power, RGB colour (name, hex or `R,G,B`), brightness
- All 213 built-in effects from the app's Mode tab, by name, `tab:number` or id, with speed,
  plus `--tour` to browse them
- **Music mode**: the app's crazy-fast beat strobe, or built-in animations pulsing with the music,
  driven by whatever the laptop is playing or by any microphone (built-in, USB webcam, …)
- The controller's own microphone mode (style, sensitivity)
- On/off timers and clock, read back from the controller
- Settings stored on the controller: wiring order, number of LEDs
- Laptop-driven animations: `rainbow`, `breathe`, `fire`, `police`
- Byte-level tests against the app's protocol (`tests/`)

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
Music mode needs PulseAudio or PipeWire (`parec`, `pactl`).

Find your strip and set its address (or edit `DEFAULT_ADDR` in the script):

```bash
lightctl --scan
export LIGHTCTL_ADDR=AA:BB:CC:DD:EE:FF
```

> Close the phone app first. The controller accepts only one BLE connection.

## Usage

```bash
# light
lightctl --power on --color red --brightness 60
lightctl --color 255,120,0                    # or ff7800, #f70, warm, purple…
lightctl --power off

# built-in effects (run on the controller, no laptop needed afterwards)
lightctl --mode 7-color-race --speed 100      # by name; the app's default speed is 100
lightctl --mode run:12                        # by app tab + number
lightctl --mode random
lightctl --list-modes water                   # search effects by word or tab
lightctl --tour strobe --interval 5           # play effects one by one, printing names

# music mode (laptop streams the app's music frames)
lightctl --music                              # react to what the laptop is playing
lightctl --music mic                          # react to the default microphone
lightctl --list-sources                       # list microphones / audio inputs
lightctl --music webcam                       # pick an input by number or part of its name
export LIGHTCTL_MIC=logitech                  # make that the mic for `--music mic`
lightctl --music mic --react mic              # calmer: the app's phone-mic beat flashes
lightctl --music --palette red purple blue    # your own colour cycle
lightctl --music --fps 20                     # even faster than the app (10/s)

# music + animation: built-in effects keep moving, brightness pulses with the music
lightctl --music --effect 7-color-running
lightctl --music --effect random --switch beat          # new animation on strong beats
lightctl --music mic --effect flow:1 water:1 --switch 8 # rotate a playlist every 8 s

# the controller's own microphone
lightctl --mic on --mic-style spectrum --mic-sens 70
lightctl --mic off

# timers and clock (stored on the controller)
lightctl --timer-on 07:30 --days weekdays --timer-off 23:00
lightctl --timer-off off                      # disable, keeping the stored time
lightctl --timers                             # read both back
lightctl --sync-clock

# settings stored on the controller
lightctl --set-wiring gbr                     # the app's "pin sequence" option
lightctl --leds 150                           # number of addressable points (10-1000)

# laptop-driven animations
lightctl --animate rainbow                    # streams until Ctrl-C
lightctl --animate fire --fast --duration 30
```

## Music mode

`--music` does what the app's Music tab does. About 10 times a second it measures
the audio level (8 kHz mono, `10*log10(mean square)` like the app). Each loud frame
gets the next colour of the app's palette (red, green, blue, yellow, magenta, cyan,
white) and each quiet frame goes dark. Frames use the app's music frame
`7e 07 05 03 R G B 20 ef`.

- **Laptop audio** (`--music` / `--music system`): quiet means below `--threshold` (40 dB).
- **Microphones**: quiet means less than 3 dB above the quietest level of the last
  30 seconds, so the strip follows the room. Use `--threshold` for a fixed level.
- `--react mic` switches to the app's phone-mic logic. It learns the background level
  and flashes only on sounds at least 5 dB louder.

`--music --effect …` goes beyond the app. The controller can't take per-LED data, so
the moving patterns come from its built-in effects, and the laptop streams brightness
(`7e 04 01 BB ff …`) 10 times a second. Brightness follows the music between the
recent quiet level (`--floor`, default 5 %) and the recent peak. `--switch beat`
changes to the next effect on a strong beat (at most once a second), and
`--switch 8` changes every 8 seconds.

Not usable for music: the controller's own mic mode (`--mic on`) did not respond to sound
on the tested unit, and changing its sensitivity in real time did not animate it.

## Protocol

GATT service `fff0`, write characteristic `fff3`. Each command is 9 bytes,
`7e <len> <cmd> … ef`, written **with response** (unacknowledged writes were
silently dropped). Streams (music, animations) use unacknowledged writes for speed.

| Action          | Frame                                   | Notes                                       |
|-----------------|-----------------------------------------|---------------------------------------------|
| Power on/off    | `7e 04 04 01 00 01 ff 00 ef` / `… 00 00 00 …` |                                       |
| Colour          | `7e 07 05 03 X Y Z 10 ef`               | byte order depends on strip wiring          |
| Music frame     | `7e 07 05 03 X Y Z 20 ef`               | what the app streams in music modes         |
| Brightness      | `7e 04 01 BB LM ff ff 00 ef`            | BB 0–100; LM `01` RGB page, `ff` effects    |
| Effect          | `7e 05 03 MM 06 ff ff 00 ef`            | MM = effect id (`--list-modes`)             |
| Effect speed    | `7e 04 02 SS ff ff ff 00 ef`            | SS 0–100                                    |
| Controller mic  | `7e 04 07 on …` / `7e 07 03 80+n 04 …` / `7e 04 06 SS …` | on/off, style, sensitivity |
| Wiring order    | `7e 06 81 a b c ff 00 ef`               | stored; app option "G B R" = `02 03 01`     |
| LED count       | `7e 07 21 lo hi 00 ff 00 ef`            | stored; 10–1000                             |
| Clock           | `7e 07 83 HH MM SS WD ff ef`            | WD 0 = Sunday                               |
| Timer           | `7e 08 82 HH MM SS T DD ef`             | T 0 on / 1 off; DD bit0 Mon…bit6 Sun, bit7 enabled; `ff ff ff` = query, then read `fff3` |
| Colour temp     | `7e 06 05 02 WW CC ff 08 ef`            | app sends it only to `MELK-…CT…` models     |
| Scene           | `7e 05 31 SS 07 ff ff 01 ef`            | `MELK-OC/OT` only; no effect on OA10        |

### Effect coverage

The 213 effects are every entry in the app's Mode tab (Basic, Curtain, Trans, Water,
Flow, Tail, Run, RunBack), using the app's own ids. The app's 28 "Scenes" are only
enabled for MELK-OC/OT controllers and one OA10 firmware. They had no effect on the
tested strip, so they are left out (send them with `--raw` if your model has them).

### Channel order

Strips are wired differently. On the tested strip the colour bytes drive **green,
blue, red** (`--order gbr`, the default). If colours come out wrong, try the other
orders, or store the order on the controller with `--set-wiring` (then use `--order rgb`):

```bash
lightctl --order rgb --color red      # or grb, brg, bgr, rbg
export LIGHTCTL_ORDER=rgb             # make it permanent
```

## Tests

```bash
.venv/bin/python -m unittest discover -s tests -v
```

Every frame builder is checked byte for byte against values transcribed from the app's
`BluetoothLEService`, along with the effect table, the music palette and the music logic.

## How it was figured out

1. `gatt_dump.py` lists the controller's GATT services and characteristics.
2. Generic ELK-BLEDOM frames (`7e 00 …`) handled colour but not effects.
3. Decompiling the Magic Lantern APK (`wl.smartled.rgb` 6.11.13) with jadx showed the
   exact frames in `BluetoothLEService`, the effect ids in the app's resources, and the
   music-mode algorithm in `MusicFragment` / `StreamingFragment`.

## Disclaimer

Not affiliated with the Magic Lantern app or the controller's manufacturer.
Interoperability project for personal use. No app code is included.

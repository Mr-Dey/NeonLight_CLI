# NeonLight CLI

Control a Bluetooth LED / neon strip (MELK / ELK-BLEDOM family, normally driven by
the **Magic Lantern** Android app) straight from a Linux terminal. Everything the app
can do with a `MELK-OA10` controller is available, plus a few extras.

Tested on a `MELK-OA10` controller from Garuda Linux (BlueZ + PipeWire) with Python 3.14
and [bleak](https://github.com/hbldh/bleak).

## Features

- **Interactive terminal UI**: run `lightctl` with no arguments to get tabs for everything, with
  switches to turn modes on and off, sliders, and a searchable effect list. It stays connected,
  so changes apply instantly
- **Several strips at once**: every command, animation, music or screen frame goes to all
  saved strips at the same moment, each with its own wiring
- Power, RGB colour (name, hex or `R,G,B`), brightness
- All 213 built-in effects from the app's Mode tab, by name, `tab:number` or id, with speed,
  plus `--tour` to browse them
- **Music mode**: the app's crazy-fast beat strobe, or built-in animations pulsing with the music,
  driven by whatever the laptop is playing or by any microphone (built-in, USB webcam, …)
- **Screen sync**: the strip glows with the colour of what's on your monitor, with smooth fades
  (Wayland/KDE via the desktop's screen-sharing portal)
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
Music mode needs PulseAudio or PipeWire (`parec`, `pactl`). Screen sync needs
`xdg-desktop-portal` and GStreamer with its PipeWire plugin (`gst-launch-1.0`, `pipewiresrc`).

Find your strips and save them under names:

```bash
lightctl --scan
lightctl --add-device monitor BE:69:32:13:B6:00
lightctl --add-device room BE:69:04:14:32:04 --order rgb   # this one is wired R-G-B
lightctl --devices
```

Saved strips live in `~/.config/lightctl/devices.json`. Without any saved strips the
CLI uses `$LIGHTCTL_ADDR`, or `DEFAULT_ADDR` from the script.

> Close the phone app first. The controller accepts only one BLE connection.

## Terminal UI

```bash
lightctl            # or: lightctl --ui, or lightctl --ui -D monitor
```

| Tab | What's there |
|-----|--------------|
| 1 Light | power switch, brightness slider, colour swatches, custom colour |
| 2 Effects | all 213 effects, filter by tab or search, Enter to play; speed; auto-tour switch |
| 3 Music | music mode switch; strobe, calm or animated style; laptop audio or any mic; effect switching |
| 4 Screen | screen sync switch; fade, colour boost, dark-scene glow, whole screen or edges |
| 5 Animations | rainbow, breathe, fire or police, with speed |
| 6 Timers | read, set and enable the on/off timers; sync clocks |
| 7 Strips | include or leave out each strip; connection status; reconnect |

Keys: `1`–`7` switch tabs, arrows move between items and change sliders, Enter/Space presses,
`x` stops the running mode, `o` toggles power, `r` reconnects, `q` quits. The mouse works too.

Only one mode (music, screen sync, animation or effect tour) runs at a time; turning one on
switches the others off. The UI keeps one Bluetooth connection to every strip, so a colour change
takes about 0.15 s instead of a 3–5 s reconnect per command. Settings are remembered in
`~/.config/lightctl/ui.json`.

## Usage

```bash
# which strips: every saved strip by default, or pick with -D / --device
lightctl --color blue                         # all strips
lightctl -D room --color blue                 # just one
lightctl -D monitor --screen                  # screen sync on the monitor strip only

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

# screen sync (strip behind the monitor)
lightctl --screen                             # first run asks which monitor to share
lightctl --screen --smooth 0.3 --saturation 3 --gain 1.6   # slower fades, vivid, brighter
lightctl --screen --fps 30                    # target rate; capped at what the link delivers
lightctl --screen --region edges --floor 0    # only the screen edges; off in dark scenes
lightctl --screen-reset                       # pick a different monitor next time

# the controller's own microphone
lightctl --mic on --mic-style spectrum --mic-sens 70
lightctl --mic off

# timers and clock (stored on the controller)
lightctl --timer-on 07:30 --days weekdays --timer-off 23:00
lightctl --timer-off off                      # disable, keeping the stored time
lightctl --timers                             # read both back
lightctl --sync-clock

# settings stored on the controller
lightctl -D monitor --set-wiring grb          # the app's "pin sequence": strip's real LED order
lightctl --leds 150                           # number of addressable points (10-1000)

# laptop-driven animations
lightctl --animate rainbow                    # streams until Ctrl-C
lightctl --animate fire --fast --duration 30
```

## Several strips

All strips are found with one Bluetooth scan and connected in parallel. Each command or
stream frame is then written to all of them at once (`asyncio.gather`). Colours the laptop
sends are re-ordered for each strip's wiring. A strip that is off or drops out is skipped
with a warning while the others carry on. Each strip has its own Bluetooth link, so
streams don't get slower with more strips.

Built-in effects are drawn by each controller itself, so for them to match:

- **Wiring:** the laptop's re-ordering can't fix the colours of built-in effects. Store a
  strip's wiring on its controller with `--set-wiring` (the app's pin-sequence setting),
  then save it with `--order rgb`. Built-in effects then show the right colours.
- **Speed:** each controller stores its own speed. With several strips the CLI sends
  speed 80 (or `--speed`) whenever it starts an effect.
- **Length:** strips with a different `--leds` count draw moving effects differently.
- Each controller starts an effect when its command arrives, so strips can be up to one
  connection interval (about 75 ms) apart.

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

## Screen sync

`--screen` asks the desktop for a screen-cast through `xdg-desktop-portal` (KDE shows its
"share your screen" picker the first time). The permission is saved in
`~/.config/lightctl/screencast-token`, so later runs start straight away. While it runs, KDE
shows its usual screen-sharing indicator.

GStreamer receives the PipeWire stream at up to 12 frames/s, shrinks each frame to 48×27
and hands it to Python. The colour is a weighted mix in linear light: vivid, bright pixels
count most, black bars and dark UI hardly count. The strip's brightness follows how bright
the screen is (never below `--floor`), and the colour fades over `--smooth` seconds.
On the tested laptop this used about 7 % of one CPU core for GStreamer plus about 2 ms of
Python per frame.

The strip shows one colour at a time (the controller has no per-LED command), so this is a
single-zone glow, not separate colours per edge.

## Protocol

GATT service `fff0`, write characteristic `fff3`. Each command is 9 bytes,
`7e <len> <cmd> … ef`, written **with response** (unacknowledged writes were
silently dropped).

Streams (music, screen, animations) are limited by the link: the strips ask for a ~75-90 ms
connection interval, so only **about 10-16 writes/s** get through (it varies with the radio
environment). Unacknowledged writes queue up instantly, and streaming faster than the link
makes a strip lag further and further behind. So streams write frames unacknowledged and,
every 5 frames, an acknowledged no-op (a timer query) in the background: its answer comes back
once the queue has drained. The time between answers is what the link really delivers, and
streams pace themselves to it (probing 10 % faster), so `--fps 60` simply runs as fast as the
link allows without building up lag. `tools/linkspeed.py` (run with sudo) shows the negotiated
interval and tries a faster one.

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

### Channel order (wiring)

Strips are wired differently, and a controller may already have a wiring stored (the app's
pin-sequence setting), which changes what you see. The reliable way:

```bash
lightctl -D monitor --set-wiring rgb --order rgb --color red   # reset: no re-mapping, plain red
lightctl -D monitor --order rgb --color red     # note the colour you see, then green, then blue
lightctl -D monitor --set-wiring grb            # store the real order you found (e.g. G-R-B)
```

`--set-wiring` stores the order on the controller (so colours **and** built-in effects are
right) and saves the strip with `--order rgb`. Without storing, `--order` re-maps colours on
the laptop side only, and built-in effects stay wrong. One tested strip turned out to be
`grb` but had "R B G" stored from before, which made it look like `gbr`.

## Tests

```bash
.venv/bin/python -m unittest discover -s tests -v
```

Every frame builder is checked byte for byte against values transcribed from the app's
`BluetoothLEService`, along with the effect table, the music palette and the music logic.
`tests/test_ui.py` drives the terminal UI headlessly with fake strips: colours, sliders, effect
search, switching modes and leaving out a strip.

## How it was figured out

1. `gatt_dump.py` lists the controller's GATT services and characteristics.
2. Generic ELK-BLEDOM frames (`7e 00 …`) handled colour but not effects.
3. Decompiling the Magic Lantern APK (`wl.smartled.rgb` 6.11.13) with jadx showed the
   exact frames in `BluetoothLEService`, the effect ids in the app's resources, and the
   music-mode algorithm in `MusicFragment` / `StreamingFragment`.

## Disclaimer

Not affiliated with the Magic Lantern app or the controller's manufacturer.
Interoperability project for personal use. No app code is included.

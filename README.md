# NeonLight CLI

Control Bluetooth LED / neon strips (MELK / ELK-BLEDOM family, normally driven by the
**Magic Lantern** Android app) from a terminal on **Linux or Windows**. Everything the app can
do with a `MELK-OA10` controller is available, plus music-reactive animations, screen sync
and several strips at once, in a command line and an interactive terminal UI.

| | Linux | Windows 10/11 |
|---|---|---|
| Bluetooth control, effects, timers, several strips, terminal UI | ✅ BlueZ | ✅ Windows Bluetooth (WinRT) |
| Music mode (computer audio or any mic) | ✅ PipeWire / PulseAudio (`parec`) | ✅ WASAPI (`soundcard`) |
| Screen sync | ✅ desktop portal + PipeWire + GStreamer | ✅ `mss` screen grabs + Pillow |
| Settings folder | `~/.config/lightctl` | `%APPDATA%\lightctl` |

Developed on Garuda Linux (KDE Wayland, PipeWire) with Python 3.14 and
[bleak](https://github.com/hbldh/bleak). The Windows back-ends are covered by tests with
stand-ins for the Windows libraries. On a real Windows 11 PC the tests pass and audio capture
works. Bluetooth hasn't been tested there yet, because that PC's USB dongle can't scan for LE
devices (see [Bluetooth adapters](#bluetooth-adapters)).

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
  driven by whatever the computer is playing or by any microphone (built-in, USB webcam, …)
- **Screen sync**: the strips glow with the colour of what's on your monitor (or one window),
  with smooth fades
- The controller's own microphone mode (style, sensitivity)
- On/off timers and clock, read back from the controller
- Settings stored on the controller: wiring order, number of LEDs
- Computer-driven animations: `rainbow`, `breathe`, `fire`, `police`
- Byte-level tests against the app's protocol (`tests/`)

> **One connection per strip.** A strip accepts a single Bluetooth connection, so close the
> phone app, and quit lightctl on any other computer, before connecting.

## Install on Linux

Needs Python 3.10+, BlueZ, and for music/screen sync PipeWire or PulseAudio (`parec`, `pactl`),
`xdg-desktop-portal` and GStreamer with its PipeWire plugin (`gst-launch-1.0`, `pipewiresrc`).

```bash
git clone git@github.com:Mr-Dey/NeonLight_CLI.git ~/projects/strip-light-cli
cd ~/projects/strip-light-cli
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
ln -sf "$PWD/lightctl.sh" ~/.local/bin/lightctl  # the launcher uses .venv next to it
lightctl --scan
```

## Install on Windows

Tested setup: Windows 10 (1709+) or 11 with a Bluetooth adapter that supports Bluetooth Low
Energy (almost all built-in ones do). The strips need **no pairing**; don't add them in the
Windows Bluetooth settings.

1. **Python** - install Python 3.13 from <https://www.python.org/downloads/windows/>
   (3.12-3.14 work too). In the installer tick **"Add python.exe to PATH"**.
2. **Git** - install Git for Windows from <https://git-scm.com/download/win> (defaults are fine).
3. **Windows Terminal** - the terminal UI looks best there (preinstalled on Windows 11;
   free in the Microsoft Store on Windows 10).
4. Open **Windows Terminal (PowerShell)** and run:

   ```powershell
   cd $HOME
   git clone https://github.com/Mr-Dey/NeonLight_CLI.git
   cd NeonLight_CLI
   py -3.13 -m venv .venv            # or: python -m venv .venv
   .venv\Scripts\pip install -r requirements.txt
   ```

   If the repository is private, Git opens a browser window to sign in to GitHub.
   Every package comes as a ready-made Windows wheel, so no compiler is needed. If PowerShell
   blocks something with an "execution policy" message, use the `.cmd` launcher as shown below
   (it doesn't need scripts enabled).
5. Make sure Bluetooth is **on** (Settings → Bluetooth & devices), the phone app is closed and
   lightctl isn't running on another computer. Then find the strips:

   ```powershell
   .\lightctl --scan
   ```

6. Save them. Both strips already have their wiring stored on their controllers, so both use
   plain `rgb`:

   ```powershell
   .\lightctl --add-device monitor BE:69:32:13:B6:00 --order rgb
   .\lightctl --add-device room BE:69:04:14:32:04 --order rgb
   .\lightctl -v --color red
   ```

7. Open the terminal UI:

   ```powershell
   .\lightctl
   ```

`.\lightctl` runs `lightctl.cmd`, which uses the `.venv` in the project folder. To type just
`lightctl` from anywhere, add the project folder to your user PATH (Settings → System → About →
Advanced system settings → Environment Variables → Path → New).

### Bluetooth adapters

The strips are Bluetooth Low Energy (LE) devices, so the computer's Bluetooth adapter has to be
able to scan for LE devices. Some cheap "Bluetooth 5.3" USB dongles can't do that on Windows: they
play audio fine but hear no LE device at all, and `lightctl --scan` says it found no Bluetooth LE
devices at all. One such dongle uses an **Actions ATS2851** chip (USB ID `10D7:B012`, listed as
"Generic Bluetooth Adapter"). Windows won't connect to an LE device it has never heard, so the
strips can't be reached through such a dongle.

To see which adapter you have:

```powershell
Get-PnpDevice -Class Bluetooth -PresentOnly | Where-Object InstanceId -like 'USB*' | Select-Object FriendlyName, InstanceId
```

`VID_10D7&PID_B012` in the InstanceId is the ATS2851. Use an adapter with a Realtek, Intel or
similar chip instead: for example a Realtek RTL8761B dongle such as the TP-Link UB500, or the
Bluetooth built into many laptops and Wi-Fi cards. Unplug the old dongle (Windows uses one
Bluetooth adapter at a time), then pair your Bluetooth speakers or headphones again.

### Windows test checklist

Run these from the project folder and note what happens (copy any error text):

```powershell
.\lightctl --scan                             # both strips found (see Bluetooth adapters if not)
.\lightctl --devices                          # both strips listed
.\lightctl -v --color red                     # both strips red
.\lightctl --mode yellow-marquee --speed 60   # both strips yellow, moving
.\lightctl --list-sources                     # speakers ("computer audio") and microphones
.\lightctl --music --duration 20              # play music first: strips strobe with it
.\lightctl --music --effect random --switch beat --duration 20
.\lightctl --list-windows                     # monitors and open windows
.\lightctl --screen --duration 20             # play a colourful video: strips follow it
.\lightctl --screen --capture "window:VLC" --duration 20   # part of a window title
.\lightctl                                    # the terminal UI: try every tab
```

If something fails, send the full output of that command plus `.\lightctl -v --color red`, your
Windows version (`winver`) and Python version (`py --version`).

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
`ui.json` in the settings folder. On Windows, tab 4 also lists your monitors and open windows
to capture.

## Usage

On Windows, type `.\lightctl` instead of `lightctl` (unless the folder is on your PATH), and set
variables with `$env:LIGHTCTL_MIC="logitech"` (PowerShell) instead of `export`.

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
lightctl --screen --capture window            # only the video player window (pick it once)
lightctl --screen --capture "window:VLC"      # Windows: the window whose title contains VLC
lightctl --screen --capture monitor:2         # Windows: a specific monitor (see --list-windows)
lightctl --screen --sample average            # plain average instead of the scene's main colour
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

Streams (screen sync, music, animations) are different: every strip has its own sender that
always takes the newest frame, paced by its own link, and faster links are handed a slightly
older frame so all strips change together (see "Protocol" below). The terminal UI's top bar
shows the updates per second each strip really gets.

## Music mode

`--music` does what the app's Music tab does. About 10 times a second it measures
the audio level (`10*log10(mean square)` of 16-bit samples, like the app; Linux records 8 kHz
mono through `parec`, Windows 48 kHz through WASAPI, same scale). Each loud frame
gets the next colour of the app's palette (red, green, blue, yellow, magenta, cyan,
white) and each quiet frame goes dark. Frames use the app's music frame
`7e 07 05 03 R G B 20 ef`.

- **Computer audio** (`--music` / `--music system`): what the default speakers play
  (a PipeWire monitor on Linux, WASAPI loopback on Windows). Quiet means below `--threshold` (40 dB).
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

**Linux:** `--screen` asks the desktop for a screen-cast through `xdg-desktop-portal` (KDE shows
its "share your screen" picker the first time). The permission is saved in the settings folder
(`screencast-token`), so later runs start straight away. While it runs, KDE shows its usual
screen-sharing indicator.

**Windows:** `mss` grabs the primary monitor (or `--capture monitor:N`, or a window with
`--capture "window:part of the title"`; plain `--capture window` follows the next window you
click). Pillow shrinks each grab with a box filter, which is the same true area average. A window
is captured as it appears on screen, so keep it uncovered; while it's minimised the strip dims.
Grabs run at up to 15 frames/s (the link carries ~10-16 updates/s anyway).

On Linux, GStreamer receives the PipeWire stream (at most 20 frames/s), shrinks each frame to 48×27
with an area-averaging filter (`bilinear2`, so every cell is the true average of its part of
the screen) and hands it to Python. `--capture window` shares a single window instead of the
monitor, so a video that isn't full screen isn't mixed with the desktop.

How the colour is picked (`--sample`):

- `dominant` (default): the hue covering the most colourful screen area, from a 24-bin hue
  histogram smoothed over time (so it doesn't flip between two similar areas). Its colour is
  averaged from those pixels. Scenes that are mostly white or grey blend into their real,
  tinted average instead of picking a small coloured spot.
- `average`: the plain average of the screen, which is what a diffuser in front of it would show.
- `vivid`: every pixel weighted by brightness × colourfulness (the original behaviour).

Colours are mixed in linear light and, with `--led-gamma 1.0` (default), sent as true light
ratios. Plain PWM LEDs respond linearly, so orange stays orange and skin stays tan; screen-style
encoding (`--led-gamma 2.2`, the original) looks paler and yellower on LEDs. Brightness follows
how bright the screen is (`--gain`, never below `--floor`). The colour fades with a
`--smooth` time constant measured in real time, so fades stay the same length whatever rate
the link manages. On the tested laptop this used about 14 % of one CPU core for GStreamer plus
a few ms of Python per frame.

The strip shows one colour at a time (the controller has no per-LED command), so this is a
single-zone glow, not separate colours per edge.

## Protocol

GATT service `fff0`, write characteristic `fff3`. Each command is 9 bytes,
`7e <len> <cmd> … ef`, written **with response** (unacknowledged writes were
silently dropped).

Streams (music, screen, animations) are limited by the link: the strips ask for a ~75-90 ms
connection interval and take about one write per interval, so only **about 10-13 writes/s**
get through (it varies with the radio environment). Unacknowledged writes queue up instantly,
so sending faster than that makes a strip lag further and further behind.

So every strip gets its own sender (`LiveFeed`). The mode (screen, music, animation) just
publishes the newest frame. Each sender takes the newest frame whenever its own link has
room, so no strip ever carries a backlog. One write in 8 is acknowledged; its round trip is
about two connection intervals, which tells the sender how far apart to space the
unacknowledged writes in between (at 80 % of what that link carries). The round trips also
show how late each strip displays a frame: a strip whose link answers faster gets a
correspondingly older frame, so all strips change colour at the same moment.
Measured on the two test strips: 9-10 updates/s each, aligned to within ~15 ms on average.
`tools/linkspeed.py` (run with sudo) shows the negotiated interval and tries a faster one.

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
search, switching modes and leaving out a strip. `tests/test_windows.py` runs the Windows
back-ends with stand-ins for `soundcard` and `mss` (audio levels, monitor and window capture,
picking sources) and the UI's Windows monitor/window pickers; it needs numpy and Pillow.

## How it was figured out

1. `gatt_dump.py` lists the controller's GATT services and characteristics.
2. Generic ELK-BLEDOM frames (`7e 00 …`) handled colour but not effects.
3. Decompiling the Magic Lantern APK (`wl.smartled.rgb` 6.11.13) with jadx showed the
   exact frames in `BluetoothLEService`, the effect ids in the app's resources, and the
   music-mode algorithm in `MusicFragment` / `StreamingFragment`.

## Disclaimer

Not affiliated with the Magic Lantern app or the controller's manufacturer.
Interoperability project for personal use. No app code is included.

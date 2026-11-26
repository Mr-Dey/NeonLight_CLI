# NeonLight CLI: notes for Claude Code

A Linux/Windows CLI and terminal UI for two Bluetooth LED strips. The strips use "MELK-OA10"
controllers, normally driven by the Magic Lantern Android app (`wl.smartled.rgb` 6.11.13); the
protocol was reverse-engineered from that APK. The user wants complete and accurate control of the
strips and has a bigger plan for them. README.md is the user-facing manual (install, usage,
protocol). This file is for the next Claude session.

## Handoff: where things stand (2026-11-26)

- Everything was built and tested in one long session on the user's Linux laptop (Garuda Linux,
  KDE Wayland, PipeWire, Python 3.14), against the real strips.
- The Windows support (commit aa47d28) was written on Linux. It was checked only with stand-ins
  for the Windows libraries (tests/test_windows.py) and by resolving the Windows wheels for Python
  3.12-3.14. **No Windows-specific code had run on real Windows before this session.**
- The user's report from Windows was "for windows its not working correctly". What we found on
  their PC (Windows 11 24H2, global Python 3.14 with no `.venv`, repo at `F:\Git\NeonLight_CLI`):
  - **Audio: fixed.** soundcard's import calls `CoInitializeEx`. On a thread where COM was
    already set up, it raised `RuntimeError('Error 0x100000001')`, so music mode and the audio
    list failed. winbackend now imports soundcard on a fresh thread (`_import_soundcard`). The
    fix came from the user's agy session and was reproduced and verified on the PC.
  - **Bluetooth: hardware.** The PC's only Bluetooth radio is a USB dongle with an Actions
    ATS2851 chip (`VID_10D7&PID_B012`). On Windows it hears no LE advertisements at all; even a
    raw WinRT `BluetoothLEAdvertisementWatcher` gets nothing, active or passive.
    - Windows can still connect to an LE device it heard recently. Its cache of unpaired LE
      devices expires, though, so connecting by address isn't a workaround (tried and dropped).
    - The user needs a different adapter (README "Bluetooth adapters"). Retest everything with
      Bluetooth once it's in.
  - The PC has no saved strips of its own: run `--add-device ... --order rgb` there.
- Testing over SSH runs in session 0. Bluetooth scanning and screen capture don't work there,
  so ask the user to run those tests in their own window.
- One change was made just before the handoff: `DEFAULT_ORDER` is now `rgb`. It was `gbr`, which
  had been stale since the monitor strip's wiring was stored on its controller. Before the change,
  a machine with no saved strips, or a strip added without `--order`, got gbr-ordered colours, so
  red showed as blue on the monitor strip. If the user tested before pulling this, re-test colours.
- Also waiting on the user (on Linux): do the two strips now change together during screen sync?
  That was the last Linux fix (LiveFeed, commit 9c167be) and they haven't confirmed it.

## First steps on Windows

1. `git pull`, then `.venv\Scripts\pip install -r requirements.txt` (see README, "Install on
   Windows"). On the user's PC there is no `.venv`: use `python` (global 3.14) instead. In Git
   Bash the paths are `.venv/Scripts/...`.
2. Run `python -m unittest discover -s tests -v`. All 52 tests use stand-ins and no Bluetooth,
   and they pass on Linux and on the user's PC. A failure here points at the install or
   environment, not the strips.
3. Close any running lightctl or UI before running commands yourself. A strip that is already
   connected stops advertising, so a second process can't find it.
4. Work through the README "Windows test checklist" one command at a time, adding `-v` where it
   helps:
   - `.\lightctl --scan` and `--devices`
   - `-v --color red`, then green, then blue
   - `--mode yellow-marquee`
   - `--list-sources`, then `--music`
   - `--list-windows`, then `--screen`
   - the UI: `.\lightctl` with no arguments

## Possible Windows problems (unverified: check each against the real symptom first)

Bluetooth (bleak 3.1 on WinRT):
- **The adapter can't scan LE (confirmed on the user's PC).** `--scan` then reports no Bluetooth
  LE devices at all. See the handoff notes above and README "Bluetooth adapters".
- **Strips are only found while advertising.** `Group._find_and_connect` (lightctl.py:724) finds
  strips with one 10 s scan, and a connected strip doesn't advertise. Each of these therefore
  looks like "not found":
  - the phone app is connected to the strip;
  - the strips were paired in Windows Settings (remove them; no pairing is needed);
  - Windows still holds the link from a crashed run (wait, or switch Bluetooth off and on).
- **Parallel connects.** Both strips connect at once (`asyncio.gather` in `_find_and_connect`).
  If one fails on WinRT (unreachable, or a timeout), try connecting them one after the other.
- **Pacing of unacknowledged writes.** WinRT completes a `response=False` write as soon as it is
  queued. LiveFeed spaces those writes using the round trip measured on the 1-in-8 acked writes,
  so pacing should hold. Check it anyway: compare the per-strip rates in the UI's top bar with
  Linux (about 9-10/s per strip) and watch for dropped frames.
- **Coarse timers.** Before Python 3.13, `time.monotonic()` (asyncio's clock) ticks in 15.6 ms
  steps on Windows. asyncio sleeps shorter than about 16 ms also round up there, so LiveFeed's
  5-10 ms polling sleeps become about 16 ms. Expect more jitter between strips, not a breakage.

Audio (soundcard 0.4.6, WASAPI):
- **"system" records the default speaker.** It is the loopback of the *default* speaker, so if
  sound plays on another device (HDMI, a headset) nothing reacts. Pick the source from
  `--list-sources`.
- **Silence gives no data.** WASAPI delivers nothing during silence. `WinAudioInput.level` returns
  `-inf` then, which is intended.
- **numpy 2.x is fine.** soundcard 0.4.6 uses `numpy.frombuffer`; numpy 2.x removed binary
  `fromstring`, and soundcard doesn't use it.

Screen (mss 10.2 GDI grabs, shrunk with Pillow's BOX filter):
- **Keep mss in the capture thread.** `WinScreenCapture` creates `mss.mss()` inside that thread
  on purpose; keep it there.
- **Cursor flicker.** mss 10.2 grabs with `SRCCOPY | CAPTUREBLT`. CAPTUREBLT is known to make
  the mouse cursor flicker on some PCs. If that happens, set `mss.windows.gdi.CAPTUREBLT = 0`
  before grabbing.
- **Black or washed-out captures.** Protected video (Netflix and other DRM streams in browsers or
  apps) can capture black. Exclusive-fullscreen games may capture black or frozen. HDR desktops
  can capture washed out.
- **CPU load.** Full-resolution GDI grabs of a 4K screen are heavy. Capture is already capped at
  15 fps on Windows (lightctl.py:1588). If CPU use or lag is still a problem, DXGI desktop
  duplication is faster, but it would be a new dependency, so ask first.
- **Display scaling.** `_dpi_aware()` and mss make the process per-monitor DPI aware, and window
  rectangles come from `DWMWA_EXTENDED_FRAME_BOUNDS`.

Console and launcher:
- **Ctrl+C prompt.** `.\lightctl` runs lightctl.cmd, so Ctrl+C ends with cmd's "Terminate batch
  job (Y/N)?" prompt. It's harmless; `.venv\Scripts\python lightctl.py ...` avoids it.
- **No extensionless `lightctl` file.** PowerShell would pick it over lightctl.cmd.
- **Python version.** Python 3.15 may lack wheels for winrt-runtime, Pillow or numpy; the README
  says to use 3.13.
- **Terminal.** Run the Textual UI in Windows Terminal; the old console host draws it badly.

## The strips

| name | where | controller | MAC | stored pin sequence | `--order` |
|---|---|---|---|---|---|
| monitor | behind the PC monitor | MELK-OA10 22 | BE:69:32:13:B6:00 | "G R B" (stored by us) | rgb |
| room | elsewhere in the room | MELK-OA10 75 | BE:69:04:14:32:04 | factory | rgb |

- **Where they're saved.** The strips are in `devices.json`. The settings folder is
  `%APPDATA%\lightctl` on Windows and `~/.config/lightctl` on Linux. It also holds `ui.json`
  (UI settings) and, on Linux, `screencast-token`.
- **Pin sequence.** Output j shows colour seq[j], for plain colours and built-in effects alike.
  The sequence is stored on each controller, so it travels with the strip to any computer.
- **One connection per strip.** Each controller accepts a single Bluetooth connection, so the
  phone app, the Linux laptop and the Windows PC can't use a strip at the same time.
- **Features this model lacks.** Firmware id is CSZGP10Y69300D13. The app offers some features
  only on other controller models, chosen by name, so they don't apply here: colour temperature
  (MELK-\*CT\*), a white channel (MELK-\*W\*) and scenes (MELK-OC/OT).

## Protocol essentials

- **GATT layout.** Service fff0; write to fff3 (`0000fff3-0000-1000-8000-00805f9b34fb`, which is
  also readable); notifications come on fff4.
- **Frames.** Every frame is 9 bytes: `7e <len> <cmd> ... ef`. Each one has a `cmd_*` builder
  starting at lightctl.py:300. tests/test_protocol.py checks every frame byte for byte against the
  app's code, and the README "Protocol" section has the full table.
- **Acked writes for single commands.** Single commands use acknowledged writes, because
  unacknowledged ones were dropped on Linux.
- **Link speed.** The link carries about one write per connection event: about 9-10 frames/s per
  strip on Linux, with an acked round trip of about 150-200 ms. The user wants 30-60 fps, which
  needs a shorter connection interval (see Open items).
- **The decompiled app.** It is not in the repo; never commit the APK or its decompile. To look
  again, pull the APK from the user's phone with adb and run jadx (ask first). The key places are:
  - `com.easylink.colorful.service.BluetoothLEService` (frames);
  - `MusicFragment` and `StreamingFragment` (music);
  - `TimingFragment` (timers);
  - `res/values/arrays.xml` (213 effect ids in 8 groups).

## Code map

- **lightctl.py**: the CLI, the protocol and everything shared.
  - **Frames and strips.** The `cmd_*` frame builders start at line 300. `Device`,
    `load_devices` and `resolve_targets` follow at about line 609.
  - **`Group`** (lightctl.py:679) does one scan, then connects to the strips in parallel.
    `write(pkt, ack)` sends to every strip not left out. `pkt` can be a function Device -> bytes,
    for per-strip colour order. `delivered` counts stream frames per strip, and the UI shows that
    rate.
  - **`LiveFeed`** (lightctl.py:778) carries every stream: screen sync, music and animations.
    - Each strip has its own sender, which always takes the newest frame.
    - One write in 8 is acknowledged, to measure the round trip. The unacknowledged writes are
      spaced at rtt/2/0.8.
    - A strip with a faster link gets a slightly older frame (by up to 0.3 s), so both strips
      change together.
    - It replaced a design with one shared pace for all strips, which left a queue on the slower
      link.
  - **Audio.**
    - `audio_sources`, `default_audio`, `choose_audio_source`, and `AudioInput`
      (lightctl.py:1106; parec on Linux, winbackend.py on Windows).
    - The level logic is in `MicReact`, `AutoGate` and `LevelNorm`.
    - `music_effects` runs a built-in effect with brightness pulsing to the music; it's the mode
      the user loves. `music` is the app's solid-colour strobe.
  - **Screen.**
    - `ScreenCapture` is the Linux capture (desktop portal + GStreamer).
    - `ScreenSampler` picks the colour (dominant, average or vivid sampling, linear-light mixing,
      `led_gamma`).
    - `open_screen_capture` chooses the capture for the platform.
    - `screen_sync` (lightctl.py:1579) captures at up to 15 fps on Windows and 20 on Linux. It
      works colours out at 25 fps or more, with fades timed in real time.
  - **The rest.** `animate`, `tour` and `main` (argparse). Running with no arguments on a
    terminal opens the UI.
- **lightui.py**: the Textual 8.2 app `LightUI`, with 7 tabs, one persistent Group, and settings
  in ui.json. Tab 4 has Windows-only monitor and window pickers (`capture_target`, line 576).
- **winbackend.py**: the Windows back-ends, imported only when `IS_WINDOWS`.
  - COM threads for audio, and `WinAudioInput` (line 89);
  - `list_windows` and `list_monitors`;
  - `WinScreenCapture` (line 245).
- **tests/**
  - test_protocol.py: frames, wiring, the music logic, the sampler, Group, LiveFeed, saved
    strips.
  - test_ui.py: the UI driven headlessly with a `FakeGroup`. Set `LIGHTUI_SCREENSHOT=dir` to save
    SVG screenshots.
  - test_windows.py: the Windows back-ends and UI with stand-ins; needs numpy and Pillow.
- **tools/linkspeed.py**: a Linux-only experiment (raw HCI, needs sudo) that asks for a shorter
  connection interval. It has never been run.
- **Launchers and helpers.** `lightctl.sh` and `lightctl.cmd` run the project's `.venv`;
  `gatt_dump.py` lists the GATT services.

## Working with this user

- **Commit and push.** After each verified change, run the full test suite, then commit and push
  to origin main (git@github.com:Mr-Dey/NeonLight_CLI.git). Use the identity on the existing
  commits (`git log -1 --format='%an <%ae>'`), set with `git config` in this clone only.
- **Controller settings need consent.** Never store a setting on a controller (`--set-wiring`,
  `--leds` and so on) without the user's explicit go-ahead for that exact change. `--leds` has
  never been sent, because the LED count is unknown.
- **Downloads.** Don't download or run external executables without asking. Never commit `.venv`.
- **Live tests.** Test one thing at a time, leave the strips in a steady state, and ask what they
  saw: they miss fast sequences. Music must be playing for music tests.
- **CPU use.** Keep it modest, because screen sync and music run for hours (the Linux laptop
  overheats).
- **Keep Linux working.** Put Windows fixes behind `IS_WINDOWS` or in winbackend.py, and keep
  the whole suite passing on Linux.
- **The user's tuned Linux UI settings** (ui.json), for reference:
  - screen: 30 fps, sample vivid, saturation 4.0, gain 1.01, LED gamma 1.0, smoothing 0;
  - music: animated, effect r-g-b-strobe, switching on the beat, floor 5, source "system";
  - effect speed 80.

## Open items

1. **Windows.** Find and fix what "not working correctly" means (see above).
2. **Linux.** The user still has to confirm that the strips now change together in screen sync
   (LiveFeed, 9c167be).
3. **30-60 fps.** The link limits each strip to about 10 frames/s, so this needs a shorter
   connection interval. The strip may refuse one either way.
   - Linux: `tools/linkspeed.py` (sudo) asks for a 7.5-15 ms interval.
   - Windows (an unverified idea): Windows 11 has
     `BluetoothLEDevice.RequestPreferredConnectionParameters(ThroughputOptimized)`. Check that the
     installed winrt package exposes it, and how to reach the BluetoothLEDevice behind bleak's
     client.
4. **Controller microphone.** The controller's own microphone mode never reacted to sound on this
   unit. Nobody has checked whether it works with the phone app.

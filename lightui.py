"""lightui - interactive terminal UI for lightctl.

Start it with `lightctl` (no arguments) or `lightctl --ui [-D names]`.

It keeps one Bluetooth connection to every strip open, so changes apply instantly. Each feature
has a tab; long-running modes (music, screen sync, animations, effect tour) have a switch, and only
one of them runs at a time. Settings are remembered in ~/.config/lightctl/ui.json.
"""
from __future__ import annotations

import argparse
import asyncio
import contextlib
import datetime as dt
import json
import os

from rich.text import Text
from textual import on, work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.color import Color
from textual.containers import Grid, Horizontal, VerticalScroll
from textual.message import Message
from textual.reactive import reactive
from textual.widgets import (Button, Footer, Header, Input, Label, OptionList, RadioButton, RadioSet,
                             RichLog, Select, Static, Switch, TabbedContent, TabPane)
from textual.widgets.option_list import Option

import lightctl as L

SETTINGS_FILE = os.path.join(L.CONFIG_DIR, "ui.json")
DEFAULTS = {
    "brightness": 100, "color": "warm", "speed": L.SYNC_SPEED,
    "tour_interval": 8.0,
    "music_style": "animated", "music_source": "system", "music_effect": "random", "music_switch": "beat",
    "music_floor": 5,
    "screen_smooth": 0.12, "screen_saturation": 1.3, "screen_region": "full", "screen_floor": 5,
    "screen_gain": 1.25, "screen_fps": 12,
    "screen_sample": "dominant", "screen_capture": "monitor", "screen_led_gamma": 1.0,
    "screen_monitor": 0, "screen_window": "",  # Windows: which monitor (0 = primary) / window title
    "animation": "rainbow", "anim_period": 6.0,
    "timer_days": "daily",
    "muted": [],
}
MUSIC_STYLES = {
    "strobe": "Strobe - a new colour on every beat (the app's Music tab)",
    "calm": "Calm - flash only on loud beats (the app's phone-mic mode)",
    "animated": "Animated - built-in effects pulsing with the beat",
}
TABS = ["light", "effects", "music", "screen", "anim", "timers", "strips"]


SCREEN_RATES = [10, 12, 15, 20, 30, 60]
CHOICES = {"music_style": list(MUSIC_STYLES), "music_switch": ["beat", "8", "15", "never"],
           "screen_fps": SCREEN_RATES, "screen_sample": list(L.SAMPLE_MODES),
           "screen_capture": ["monitor", "window"],
           "screen_region": ["full", "edges"], "animation": list(L.ANIMATIONS),
           "timer_days": ["daily", "weekdays", "weekends"]}


def load_settings() -> dict:
    s = dict(DEFAULTS)
    try:
        with open(SETTINGS_FILE) as f:
            s.update({k: v for k, v in json.load(f).items() if k in DEFAULTS})
    except (OSError, ValueError):
        pass
    for key, allowed in CHOICES.items():
        if s[key] not in allowed:
            s[key] = DEFAULTS[key]
    if s["music_effect"] != "random" and s["music_effect"] not in L.MODES:
        s["music_effect"] = "random"
    for key in ("brightness", "speed", "tour_interval", "music_floor", "screen_smooth",
                "screen_saturation", "screen_floor", "screen_gain", "screen_led_gamma", "anim_period"):
        if not isinstance(s[key], (int, float)):
            s[key] = DEFAULTS[key]
    if not isinstance(s["muted"], list):
        s["muted"] = []
    if not isinstance(s["screen_monitor"], int) or s["screen_monitor"] < 0:
        s["screen_monitor"] = 0
    if not isinstance(s["screen_window"], str):
        s["screen_window"] = ""
    return s


def save_settings(s: dict) -> None:
    try:
        os.makedirs(os.path.dirname(SETTINGS_FILE), exist_ok=True)
        tmp = SETTINGS_FILE + ".tmp"
        with open(tmp, "w") as f:
            json.dump(s, f, indent=2)
        os.replace(tmp, SETTINGS_FILE)
    except OSError:
        pass


class Level(Static, can_focus=True):
    """A slider. Left/right to change (shift = fine), Home/End for the ends, or click on it."""

    BINDINGS = [
        Binding("left", "step(-1)", show=False), Binding("right", "step(1)", show=False),
        Binding("shift+left", "step(-0.2)", show=False), Binding("shift+right", "step(0.2)", show=False),
        Binding("home", "jump(0)", show=False), Binding("end", "jump(1)", show=False),
    ]
    DEFAULT_CSS = """
    Level { height: 3; width: 1fr; content-align: left middle; padding: 0 1; }
    Level:focus { background: $boost; }
    """
    value: reactive[float] = reactive(0.0)

    class Changed(Message):
        def __init__(self, level: "Level", value: float) -> None:
            super().__init__()
            self.level, self.value = level, value

        @property
        def control(self) -> "Level":
            return self.level

    def __init__(self, value: float, lo: float = 0, hi: float = 100, step: float = 5,
                 fmt: str = "{:.0f}%", **kw) -> None:
        super().__init__(**kw)
        self.lo, self.hi, self.step_size, self.fmt = lo, hi, step, fmt
        self.set_reactive(Level.value, min(hi, max(lo, value)))

    def _bar_width(self) -> int:
        return max(10, self.size.width - 12)

    def render(self) -> Text:
        w = self._bar_width()
        n = round((self.value - self.lo) / (self.hi - self.lo) * w)
        t = Text()
        t.append("━" * n, style="bold magenta")
        t.append("─" * (w - n), style="dim")
        t.append(f" {self.fmt.format(self.value):>7}")
        return t

    def _set(self, v: float) -> None:
        v = min(self.hi, max(self.lo, round(v / self.step_size * 5) * self.step_size / 5))
        if abs(v - self.value) > 1e-9:
            self.value = v
            self.post_message(self.Changed(self, v))

    def action_step(self, k: float) -> None:
        self._set(self.value + k * self.step_size)

    def action_jump(self, frac: float) -> None:
        self._set(self.lo + frac * (self.hi - self.lo))

    def on_click(self, event) -> None:
        x = event.x - 1  # left padding
        self._set(self.lo + max(0.0, min(1.0, x / self._bar_width())) * (self.hi - self.lo))


class LightUI(App):
    TITLE = "NeonLight"
    SUB_TITLE = "lightctl"
    CSS = """
    #status { height: 1; padding: 0 1; background: $panel; }
    TabbedContent { height: 1fr; }
    TabPane { padding: 0; }
    .row { height: auto; margin: 0 1; }
    .lbl { width: 18; height: 3; content-align: left middle; }
    .hint { color: $text-muted; margin: 0 1; width: 100%; height: auto; }
    .after { height: 3; content-align: left middle; color: $text-muted; }
    .section { margin: 1 1 0 1; text-style: bold; color: $accent; }
    Switch { margin: 0 1 0 0; }
    #swatches { grid-size: 6; grid-gutter: 1 1; height: auto; margin: 0 1; }
    .swatch { width: 100%; min-width: 6; }
    #custom-color { width: 1fr; }
    #effect-tab { width: 24; }
    #effect-search { width: 1fr; }
    #effect-list { height: 1fr; min-height: 8; margin: 0 1; }
    #tour-interval { width: 10; }
    #music-source { width: 1fr; }
    #music-effect { width: 1fr; }
    #music-switch { width: 30; }
    #screen-fps { width: 36; }
    #screen-monitor, #screen-window { width: 1fr; }
    RadioSet { margin: 0 1; width: auto; }
    #timer-view { margin: 1 1; height: auto; }
    .timer-in { width: 12; }
    #timer-days { width: 24; }
    .strip-status { height: 3; content-align: left middle; width: 1fr; }
    #log { height: 7; border-top: hkey $panel; }
    """
    BINDINGS = [
        Binding("q", "quit", "Quit"),
        Binding("x", "stop", "Stop mode"),
        Binding("o", "power", "Power on/off"),
        Binding("r", "reconnect", "Reconnect"),
        *[Binding(str(i + 1), f"tab('{t}')", show=False) for i, t in enumerate(TABS)],
    ]
    MODE_SWITCHES = {"music": "#music-on", "screen": "#screen-on", "anim": "#anim-on", "tour": "#tour-on"}
    MODE_NAMES = {"music": "Music", "screen": "Screen sync", "anim": "Animation", "tour": "Effect tour"}

    def __init__(self, devices: list[L.Device]) -> None:
        super().__init__()
        self.devices = devices
        self.group = L.Group(devices)
        self.s = load_settings()
        self.group.muted = {n for n in self.s["muted"] if any(d.name == n for d in devices)}
        self.mode_task: asyncio.Task | None = None
        self.mode_name: str | None = None
        self.last_kind = "color"  # what the strips show outside modes: "color" or "effect"
        # what we last sent, to skip writes that change nothing (reset on reconnect and by modes)
        self.sent: dict[str, object] = {}
        self.connecting = False
        self._closed = False
        self.rates: dict[str, float] = {}  # updates per second each strip really gets while a mode runs
        self._rate_mark: tuple[float, dict] | None = None

    # ---------- layout ----------

    def compose(self) -> ComposeResult:
        s = self.s
        yield Header(show_clock=True)
        yield Static(id="status")
        with TabbedContent(initial="light"):
            with TabPane("1 Light", id="light"):
                with VerticalScroll():
                    with Horizontal(classes="row"):
                        yield Label("Power", classes="lbl")
                        yield Switch(value=True, id="power")
                    with Horizontal(classes="row"):
                        yield Label("Brightness", classes="lbl")
                        yield Level(s["brightness"], id="brightness")
                    yield Label("Colour", classes="section")
                    with Grid(id="swatches"):
                        for name in L.COLORS:
                            yield Button(name, id=f"col-{name}", classes="swatch")
                    with Horizontal(classes="row"):
                        yield Input(placeholder="custom colour: ff7800, #f70 or 255,120,0", id="custom-color")
                        yield Button("Apply", id="apply-color", variant="primary")
            with TabPane("2 Effects", id="effects"):
                with Horizontal(classes="row"):
                    yield Select([("All tabs", "")] + [(g.title(), g) for g in L.group_lists()],
                                 value="", allow_blank=False, id="effect-tab")
                    yield Input(placeholder="search effects…", id="effect-search")
                yield OptionList(id="effect-list")
                with Horizontal(classes="row"):
                    yield Label("Speed", classes="lbl")
                    yield Level(s["speed"], id="speed")
                with Horizontal(classes="row"):
                    yield Label("Tour this list", classes="lbl")
                    yield Switch(value=False, id="tour-on")
                    yield Label("seconds each", classes="lbl")
                    yield Input(f"{s['tour_interval']:g}", id="tour-interval", type="number")
            with TabPane("3 Music", id="music"):
                with VerticalScroll():
                    with Horizontal(classes="row"):
                        yield Label("Music mode", classes="lbl")
                        yield Switch(value=False, id="music-on")
                    yield Label("Style", classes="section")
                    with RadioSet(id="music-style"):
                        for key, text in MUSIC_STYLES.items():
                            yield RadioButton(text, value=s["music_style"] == key, id=f"ms-{key}")
                    yield Label("Listen to", classes="section")
                    with Horizontal(classes="row"):
                        yield Select(self.source_options(), value=self.valid_source(s["music_source"]),
                                     allow_blank=False, id="music-source")
                        yield Button("↻", id="refresh-sources")
                    yield Label("Animated style", classes="section")
                    with Horizontal(classes="row"):
                        yield Select([("Random effects", "random")] + [(n, n) for n in L.MODES],
                                     value=s["music_effect"] if s["music_effect"] in L.MODES else "random",
                                     allow_blank=False, id="music-effect")
                        yield Select([("Switch on strong beats", "beat"), ("Every 8 s", "8"),
                                      ("Every 15 s", "15"), ("Never switch", "never")],
                                     value=s["music_switch"], allow_blank=False, id="music-switch")
                    with Horizontal(classes="row"):
                        yield Label("Between beats", classes="lbl")
                        yield Level(s["music_floor"], 0, 50, 1, id="music-floor")
                    yield Label("Plug in a webcam or USB mic, press ↻ and pick it under 'Listen to'.",
                                classes="hint")
            with TabPane("4 Screen", id="screen"):
                with VerticalScroll():
                    with Horizontal(classes="row"):
                        yield Label("Screen sync", classes="lbl")
                        yield Switch(value=False, id="screen-on")
                    with Horizontal(classes="row"):
                        yield Label("Fade", classes="lbl")
                        yield Level(s["screen_smooth"], 0, 1, 0.04, "{:.2f}s", id="screen-smooth")
                    with Horizontal(classes="row"):
                        yield Label("Colour boost", classes="lbl")
                        yield Level(s["screen_saturation"], 0.5, 4.0, 0.1, "{:.1f}x", id="screen-saturation")
                    with Horizontal(classes="row"):
                        yield Label("Brightness boost", classes="lbl")
                        yield Level(s["screen_gain"], 0.5, 3.0, 0.05, "{:.2f}x", id="screen-gain")
                    with Horizontal(classes="row"):
                        yield Label("Update rate", classes="lbl")
                        yield Select([(f"{r} per second" + (" (default)" if r == 12 else ""), r)
                                      for r in SCREEN_RATES], value=s["screen_fps"], allow_blank=False,
                                     id="screen-fps")
                    with Horizontal(classes="row"):
                        yield Label("Dark scenes", classes="lbl")
                        yield Level(s["screen_floor"], 0, 50, 1, id="screen-floor")
                    yield Label("Colour from", classes="section")
                    with RadioSet(id="screen-sample"):
                        yield RadioButton("Main colour of the scene (most accurate)",
                                          value=s["screen_sample"] == "dominant", id="ss-dominant")
                        yield RadioButton("Average of the screen", value=s["screen_sample"] == "average",
                                          id="ss-average")
                        yield RadioButton("Brightest, most vivid spots (original)",
                                          value=s["screen_sample"] == "vivid", id="ss-vivid")
                    with Horizontal(classes="row"):
                        yield Label("LED gamma", classes="lbl")
                        yield Level(s["screen_led_gamma"], 1.0, 2.4, 0.1, "{:.1f}", id="screen-led-gamma")
                    yield Label("1.0 sends the true light mix (orange stays orange on LEDs); higher looks paler, "
                                "like the original.", classes="hint")
                    yield Label("Capture", classes="section")
                    with RadioSet(id="screen-capture"):
                        yield RadioButton("Whole monitor", value=s["screen_capture"] == "monitor", id="sc-monitor")
                        yield RadioButton("One window - e.g. the video player" +
                                          ("" if L.IS_WINDOWS else " (pick it in the dialog)"),
                                          value=s["screen_capture"] == "window", id="sc-window")
                    if L.IS_WINDOWS:
                        with Horizontal(classes="row"):
                            yield Label("Monitor", classes="lbl")
                            yield Select(self.monitor_options(), value=self.valid_monitor(s["screen_monitor"]),
                                         allow_blank=False, id="screen-monitor")
                        with Horizontal(classes="row"):
                            yield Label("Window", classes="lbl")
                            yield Select(self.window_options(), value=self.valid_window(s["screen_window"]),
                                         allow_blank=False, id="screen-window")
                            yield Button("↻", id="refresh-windows")
                    yield Label("Sample", classes="section")
                    with RadioSet(id="screen-region"):
                        yield RadioButton("Whole screen", value=s["screen_region"] == "full", id="sr-full")
                        yield RadioButton("Edges only", value=s["screen_region"] == "edges", id="sr-edges")
                    yield Label(("A window is captured as it appears on screen: keep it uncovered (minimised = dark). "
                                 if L.IS_WINDOWS else "The first time, your desktop asks which screen to share. ")
                                + "Tip: switch off strips that aren't behind the monitor in tab 7.", classes="hint")
                    yield Label("Each strip's Bluetooth link carries about 10-16 updates per second, so higher "
                                "rates run as fast as the link allows; the top bar shows the real rate.", classes="hint")
            with TabPane("5 Animations", id="anim"):
                with VerticalScroll():
                    with Horizontal(classes="row"):
                        yield Label("Animation", classes="lbl")
                        yield Switch(value=False, id="anim-on")
                    with RadioSet(id="anim-kind"):
                        for name in L.ANIMATIONS:
                            yield RadioButton(name, value=s["animation"] == name, id=f"an-{name}")
                    with Horizontal(classes="row"):
                        yield Label("Cycle", classes="lbl")
                        yield Level(s["anim_period"], 0.5, 20, 0.5, "{:.1f}s", id="anim-period")
                    yield Label("'breathe' uses the colour picked in tab 1.", classes="hint")
            with TabPane("6 Timers", id="timers"):
                with VerticalScroll():
                    yield Static("Press 'Read' to load the timers stored on the strips.", id="timer-view")
                    with Horizontal(classes="row"):
                        yield Label("Switch on at", classes="lbl")
                        yield Input(placeholder="07:30", id="timer-on-time", classes="timer-in")
                        yield Switch(value=False, id="timer-on-enabled")
                        yield Label("enabled", classes="after")
                    with Horizontal(classes="row"):
                        yield Label("Switch off at", classes="lbl")
                        yield Input(placeholder="23:00", id="timer-off-time", classes="timer-in")
                        yield Switch(value=False, id="timer-off-enabled")
                        yield Label("enabled", classes="after")
                    with Horizontal(classes="row"):
                        yield Label("Days", classes="lbl")
                        yield Select([("Every day", "daily"), ("Weekdays", "weekdays"), ("Weekends", "weekends")],
                                     value=s["timer_days"], allow_blank=False, id="timer-days")
                    with Horizontal(classes="row"):
                        yield Button("Read", id="timers-read")
                        yield Button("Save timers", id="timers-save", variant="primary")
                        yield Button("Sync clock", id="clock-sync")
            with TabPane("7 Strips", id="strips"):
                with VerticalScroll():
                    for i, d in enumerate(self.devices):
                        with Horizontal(classes="row"):
                            yield Switch(value=d.name not in self.group.muted, id=f"strip-{i}",
                                         classes="strip-switch")
                            yield Static(id=f"strip-status-{i}", classes="strip-status")
                    with Horizontal(classes="row"):
                        yield Button("Reconnect missing strips", id="reconnect", variant="primary")
                    yield Label("Switched-off strips are left out of everything until switched on again. "
                                "Add strips with: lightctl --add-device NAME MAC (then restart).", classes="hint")
        yield RichLog(id="log", max_lines=300, wrap=True, markup=False, highlight=False)
        yield Footer()

    def source_options(self) -> list[tuple[str, str]]:
        opts = [("Computer audio - whatever is playing", "system"), ("Default microphone", "mic")]
        with contextlib.suppress(SystemExit, OSError, ValueError, RuntimeError):
            for src in L.audio_sources():
                if not src["monitor"]:
                    opts.append((f"Microphone: {src['desc']}", src["name"]))
        return opts

    def monitor_options(self) -> list[tuple[str, int]]:
        opts = [("Primary monitor", 0)]
        with contextlib.suppress(Exception):
            import winbackend
            for m in winbackend.list_monitors():
                opts.append((f"Monitor {m['index']}: {m['width']}x{m['height']}"
                             + (" (primary)" if m["primary"] else "") + (f" {m['name']}" if m["name"] else ""),
                             m["index"]))
        return opts

    def valid_monitor(self, value: int) -> int:
        return value if value in [v for _, v in self.monitor_options()] else 0

    def window_options(self) -> list[tuple[str, str]]:
        opts = [("The next window I click (10 s)", "")]
        with contextlib.suppress(Exception):
            import winbackend
            seen = set()
            for w in winbackend.list_windows():
                if w["title"] not in seen:
                    seen.add(w["title"])
                    opts.append((w["title"][:70], w["title"]))
        return opts

    def valid_window(self, value: str) -> str:
        return value if value in [v for _, v in self.window_options()] else ""

    def valid_source(self, value: str) -> str:
        return value if value in [v for _, v in self.source_options()] else "system"

    # ---------- start-up, logging, status ----------

    def on_mount(self) -> None:
        L.log = self.log_line
        for name, rgb in L.COLORS.items():
            btn = self.query_one(f"#col-{name}", Button)
            btn.styles.background = Color(*rgb)
            btn.styles.color = Color(0, 0, 0) if sum(rgb) > 380 else Color(255, 255, 255)
        self.fill_effects()
        self.refresh_status()
        self.set_interval(3, self.check_strips)
        self.connect()

    def log_line(self, msg: str, err: bool = True) -> None:
        with contextlib.suppress(Exception):
            style = "red" if msg.startswith("lightctl:") or "error" in msg.lower() else ""
            self.query_one("#log", RichLog).write(Text(msg, style=style))

    def refresh_status(self) -> None:
        parts = []
        for d in self.devices:
            if d.name in self.group.muted:
                parts.append(f"[dim]○ {d.name} (off)[/]")
            elif d in self.group.clients:
                parts.append(f"[green]●[/] {d.name}")
            else:
                parts.append(f"[red]●[/] {d.name}" + (" [dim]connecting…[/]" if self.connecting else ""))
        mode = self.MODE_NAMES.get(self.mode_name or "", "")
        rate = "".join(f" · {k} {v:.1f}/s" for k, v in self.rates.items()) if mode else ""
        status = "   ".join(parts) + ("   │   [b]" + mode + "[/] running" + rate + "  (x to stop)" if mode else "")
        with contextlib.suppress(Exception):
            self.query_one("#status", Static).update(status)
            for i, d in enumerate(self.devices):
                state = ("left out" if d.name in self.group.muted else
                         "connected" if d in self.group.clients else "not connected")
                self.query_one(f"#strip-status-{i}", Static).update(
                    f"[b]{d.name}[/]  {d.addr}  wiring {d.order}  -  {state}")

    @work(exclusive=True, group="connect")
    async def connect(self) -> None:
        self.connecting = True
        self.refresh_status()
        self.log_line(f"connecting to {', '.join(d.name for d in self.devices)}…")
        try:
            new = await self.group.connect_missing()
        except Exception as e:  # noqa: BLE001
            self.log_line(f"lightctl: connect failed: {e}")
            new = []
        self.connecting = False
        if new:
            self.sent.clear()
            self.log_line(f"connected: {', '.join(d.name for d in new)}")
        self.refresh_status()

    def check_strips(self) -> None:
        for d in self.group.prune():
            self.log_line(f"lightctl: {d.name}: connection lost (press r to reconnect)")
        now = asyncio.get_running_loop().time()
        snap = dict(self.group.delivered)
        if self.mode_name and self._rate_mark is not None and now > self._rate_mark[0]:
            t0, before = self._rate_mark
            self.rates = {k: max(0, v - before.get(k, 0)) / (now - t0) for k, v in snap.items()}
        else:
            self.rates = {}
        self._rate_mark = (now, snap) if self.mode_name else None
        self.refresh_status()

    # ---------- helpers ----------

    def save(self, **kw) -> None:
        self.s.update(kw)
        save_settings(self.s)

    def ready(self) -> bool:
        if not self.group.clients:
            self.log_line("lightctl: no strip connected yet (press r to reconnect)")
            return False
        return True

    async def write(self, *pkts) -> None:
        for pkt in pkts:
            try:
                await self.group.write(pkt)
            except SystemExit as e:
                self.log_line(str(e))
                return

    def set_switch(self, selector: str, value: bool) -> None:
        with contextlib.suppress(Exception):
            sw = self.query_one(selector, Switch)
            if sw.value != value:
                with self.prevent(Switch.Changed):
                    sw.value = value

    # ---------- modes: only one runs at a time ----------

    async def start_mode(self, name: str) -> None:
        await self.stop_mode(keep=name)
        if not self.ready():
            self.set_switch(self.MODE_SWITCHES[name], False)
            return
        factory = {"music": self.music_job, "screen": self.screen_job,
                   "anim": self.anim_job, "tour": self.tour_job}[name]()
        self.mode_name = name
        self.sent.clear()  # modes send their own power / brightness / speed
        self.mode_task = asyncio.create_task(self._run_mode(name, factory))
        self.log_line(f"{self.MODE_NAMES[name]} started")
        self.refresh_status()

    async def _run_mode(self, name: str, factory) -> None:
        try:
            await factory()
        except SystemExit as e:
            self.log_line(str(e))
        except asyncio.CancelledError:
            raise
        except Exception as e:  # noqa: BLE001
            self.log_line(f"lightctl: {self.MODE_NAMES[name]} failed: {e!r}")
        finally:
            if self.mode_name == name and self.mode_task is asyncio.current_task():
                self.mode_name, self.mode_task = None, None
                self.set_switch(self.MODE_SWITCHES[name], False)
                self.refresh_status()

    async def stop_mode(self, keep: str | None = None) -> None:
        task, name = self.mode_task, self.mode_name
        self.mode_task, self.mode_name = None, None
        if task is not None and not task.done():
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await task
            self.log_line(f"{self.MODE_NAMES[name]} stopped")
        for mode, sel in self.MODE_SWITCHES.items():
            if mode != keep:
                self.set_switch(sel, False)
        self.refresh_status()

    @work(exclusive=True, group="restart")
    async def restart_soon(self, mode: str) -> None:
        """Options changed while a mode runs: restart it once the user stops fiddling."""
        await asyncio.sleep(0.6)
        if self.mode_name == mode:
            await self.start_mode(mode)

    def music_job(self):
        s = self.s
        src = s["music_source"]
        if s["music_style"] == "animated":
            effects = [s["music_effect"]]
            switch = None if s["music_switch"] == "never" else s["music_switch"]
            return lambda: L.music_effects(self.group, src, 10, effects, switch, int(s["speed"]),
                                           int(s["music_floor"]), 0, False)
        react = "music" if s["music_style"] == "strobe" else "mic"
        return lambda: L.music(self.group, src, react, 10, L.MUSIC_PALETTE, None, 0, False)

    def capture_target(self) -> str:
        s = self.s
        if s["screen_capture"] == "window":
            return f"window:{s['screen_window']}" if L.IS_WINDOWS and s["screen_window"] else "window"
        return f"monitor:{s['screen_monitor']}" if L.IS_WINDOWS and s["screen_monitor"] else "monitor"

    def screen_job(self):
        s = self.s
        target = self.capture_target()
        return lambda: L.screen_sync(self.group, int(s["screen_fps"]), s["screen_smooth"], s["screen_region"],
                                     s["screen_saturation"], int(s["screen_floor"]), 0, False,
                                     s["screen_gain"], s["screen_sample"], s["screen_led_gamma"], target)

    def anim_job(self):
        s = self.s
        base = L.COLORS.get(s["color"]) if isinstance(s["color"], str) else tuple(s["color"])
        return lambda: L.animate(self.group, s["animation"], 0, s["anim_period"], base, 20, False, True)

    def tour_job(self):
        s = self.s
        flt = self.query_one("#effect-search", Input).value.strip() or self.query_one("#effect-tab", Select).value
        return lambda: L.tour(self.group, flt or None, float(s["tour_interval"]), int(s["speed"]), 0)

    # ---------- actions (keys) ----------

    async def action_stop(self) -> None:
        await self.stop_mode()

    def action_power(self) -> None:
        sw = self.query_one("#power", Switch)
        sw.value = not sw.value

    def action_reconnect(self) -> None:
        self.connect()

    def action_tab(self, tab: str) -> None:
        self.query_one(TabbedContent).active = tab

    async def action_quit(self) -> None:
        await self.shutdown()
        self.exit()

    async def shutdown(self) -> None:
        if self._closed:
            return
        self._closed = True
        await self.stop_mode()
        with contextlib.suppress(Exception):
            await self.group.__aexit__(None, None, None)

    async def on_unmount(self) -> None:
        await self.shutdown()

    # ---------- tab 1: light ----------

    @on(Switch.Changed, "#power")
    async def power_changed(self, ev: Switch.Changed) -> None:
        if not self.ready():
            return
        if not ev.value:
            await self.stop_mode()
        await self.write(L.cmd_power(ev.value))
        self.sent["power"] = ev.value
        self.log_line(f"power {'on' if ev.value else 'off'}")

    @on(Level.Changed, "#brightness")
    def brightness_changed(self, ev: Level.Changed) -> None:
        self.save(brightness=int(ev.value))
        self.send_brightness(int(ev.value))

    @work(exclusive=True, group="brightness")
    async def send_brightness(self, v: int) -> None:
        await asyncio.sleep(0.08)  # coalesce key repeats; only the latest value is sent
        if self.group.clients:
            lm = 0x01 if self.last_kind == "color" else 0xFF
            await self.write(L.cmd_brightness(v, lm))
            self.sent["brightness"] = (v, lm)

    async def apply_color(self, rgb: tuple[int, int, int], label: str) -> None:
        if not self.ready():
            return
        await self.stop_mode()
        self.set_switch("#power", True)
        self.last_kind = "color"
        pkts = [] if self.sent.get("power") is True else [L.cmd_power(True)]
        pkts.append(lambda d: L.cmd_color(*rgb, order=d.order))
        bright = (int(self.s["brightness"]), 0x01)
        if self.sent.get("brightness") != bright:
            pkts.append(L.cmd_brightness(*bright))
        await self.write(*pkts)
        self.sent.update(power=True, brightness=bright)
        self.log_line(f"colour {label}")

    @on(Button.Pressed, ".swatch")
    async def swatch(self, ev: Button.Pressed) -> None:
        name = ev.button.id.removeprefix("col-")
        self.save(color=name)
        await self.apply_color(L.COLORS[name], name)

    @on(Button.Pressed, "#apply-color")
    @on(Input.Submitted, "#custom-color")
    async def custom_color(self) -> None:
        text = self.query_one("#custom-color", Input).value
        try:
            rgb = L.parse_color(text)
        except argparse.ArgumentTypeError as e:
            self.log_line(f"lightctl: {e}")
            return
        self.save(color=list(rgb))
        await self.apply_color(rgb, text)

    # ---------- tab 2: effects ----------

    def fill_effects(self) -> None:
        tab = self.query_one("#effect-tab", Select).value or ""
        word = self.query_one("#effect-search", Input).value.strip().lower()
        ol = self.query_one("#effect-list", OptionList)
        ol.clear_options()
        opts = []
        for group, lst in L.group_lists().items():
            if tab and group != tab:
                continue
            for i, (name, mid) in enumerate(lst, 1):
                if word and word not in name:
                    continue
                opts.append(Option(f"{group + ':' + str(i):<12} {name}", id=name))
        ol.add_options(opts)

    @on(Select.Changed, "#effect-tab")
    @on(Input.Changed, "#effect-search")
    def effect_filter(self) -> None:
        self.fill_effects()

    @on(OptionList.OptionSelected, "#effect-list")
    async def effect_chosen(self, ev: OptionList.OptionSelected) -> None:
        if not self.ready():
            return
        await self.stop_mode()
        name = ev.option.id
        self.set_switch("#power", True)
        self.last_kind = "effect"
        pkts = [] if self.sent.get("power") is True else [L.cmd_power(True)]
        pkts.append(L.cmd_mode(L.MODES[name][1]))
        if self.sent.get("speed") != int(self.s["speed"]):
            pkts.append(L.cmd_speed(int(self.s["speed"])))
        await self.write(*pkts)
        self.sent.update(power=True, speed=int(self.s["speed"]))
        self.log_line(f"effect {name}")

    @on(Level.Changed, "#speed")
    def speed_changed(self, ev: Level.Changed) -> None:
        self.save(speed=int(ev.value))
        self.send_speed(int(ev.value))

    @work(exclusive=True, group="speed")
    async def send_speed(self, v: int) -> None:
        await asyncio.sleep(0.08)
        if self.group.clients:
            await self.write(L.cmd_speed(v))
            self.sent["speed"] = v

    @on(Switch.Changed, "#tour-on")
    async def tour_switch(self, ev: Switch.Changed) -> None:
        await (self.start_mode("tour") if ev.value else self.stop_mode())

    @on(Input.Changed, "#tour-interval")
    def tour_interval(self, ev: Input.Changed) -> None:
        with contextlib.suppress(ValueError):
            v = float(ev.value)
            if v >= 1:
                self.save(tour_interval=v)
                if self.mode_name == "tour":
                    self.restart_soon("tour")

    # ---------- tab 3: music ----------

    @on(Switch.Changed, "#music-on")
    async def music_switch(self, ev: Switch.Changed) -> None:
        await (self.start_mode("music") if ev.value else self.stop_mode())

    @on(RadioSet.Changed, "#music-style")
    def music_style(self, ev: RadioSet.Changed) -> None:
        self.save(music_style=ev.pressed.id.removeprefix("ms-"))
        if self.mode_name == "music":
            self.restart_soon("music")

    @on(Select.Changed, "#music-source")
    @on(Select.Changed, "#music-effect")
    @on(Select.Changed, "#music-switch")
    def music_option(self, ev: Select.Changed) -> None:
        key = {"music-source": "music_source", "music-effect": "music_effect",
               "music-switch": "music_switch"}[ev.select.id]
        self.save(**{key: ev.value})
        if self.mode_name == "music":
            self.restart_soon("music")

    @on(Level.Changed, "#music-floor")
    def music_floor(self, ev: Level.Changed) -> None:
        self.save(music_floor=int(ev.value))
        if self.mode_name == "music" and self.s["music_style"] == "animated":
            self.restart_soon("music")

    @on(Button.Pressed, "#refresh-sources")
    def refresh_sources(self) -> None:
        sel = self.query_one("#music-source", Select)
        sel.set_options(self.source_options())
        sel.value = self.valid_source(self.s["music_source"])
        self.log_line("audio inputs refreshed")

    # ---------- tab 4: screen ----------

    @on(Switch.Changed, "#screen-on")
    async def screen_switch(self, ev: Switch.Changed) -> None:
        await (self.start_mode("screen") if ev.value else self.stop_mode())

    @on(Level.Changed, "#screen-smooth")
    @on(Level.Changed, "#screen-saturation")
    @on(Level.Changed, "#screen-floor")
    @on(Level.Changed, "#screen-gain")
    @on(Level.Changed, "#screen-led-gamma")
    def screen_option(self, ev: Level.Changed) -> None:
        key = {"screen-smooth": "screen_smooth", "screen-saturation": "screen_saturation",
               "screen-floor": "screen_floor", "screen-gain": "screen_gain",
               "screen-led-gamma": "screen_led_gamma"}[ev.level.id]
        self.save(**{key: round(ev.value, 3)})
        if self.mode_name == "screen":
            self.restart_soon("screen")

    @on(Select.Changed, "#screen-fps")
    def screen_fps(self, ev: Select.Changed) -> None:
        self.save(screen_fps=int(ev.value))
        if self.mode_name == "screen":
            self.restart_soon("screen")

    @on(Select.Changed, "#screen-monitor")
    @on(Select.Changed, "#screen-window")
    def screen_target(self, ev: Select.Changed) -> None:
        if ev.select.id == "screen-monitor":
            self.save(screen_monitor=int(ev.value))
        else:
            self.save(screen_window=str(ev.value))
        if self.mode_name == "screen":
            self.restart_soon("screen")

    @on(Button.Pressed, "#refresh-windows")
    def refresh_windows(self) -> None:
        sel = self.query_one("#screen-window", Select)
        sel.set_options(self.window_options())
        sel.value = self.valid_window(self.s["screen_window"])
        self.log_line("window list refreshed")

    @on(RadioSet.Changed, "#screen-region")
    @on(RadioSet.Changed, "#screen-sample")
    @on(RadioSet.Changed, "#screen-capture")
    def screen_choice(self, ev: RadioSet.Changed) -> None:
        key, prefix = {"screen-region": ("screen_region", "sr-"), "screen-sample": ("screen_sample", "ss-"),
                       "screen-capture": ("screen_capture", "sc-")}[ev.radio_set.id]
        self.save(**{key: ev.pressed.id.removeprefix(prefix)})
        if self.mode_name == "screen":
            self.restart_soon("screen")

    # ---------- tab 5: animations ----------

    @on(Switch.Changed, "#anim-on")
    async def anim_switch(self, ev: Switch.Changed) -> None:
        await (self.start_mode("anim") if ev.value else self.stop_mode())

    @on(RadioSet.Changed, "#anim-kind")
    def anim_kind(self, ev: RadioSet.Changed) -> None:
        self.save(animation=ev.pressed.id.removeprefix("an-"))
        if self.mode_name == "anim":
            self.restart_soon("anim")

    @on(Level.Changed, "#anim-period")
    def anim_period(self, ev: Level.Changed) -> None:
        self.save(anim_period=ev.value)
        if self.mode_name == "anim":
            self.restart_soon("anim")

    # ---------- tab 6: timers ----------

    @on(Button.Pressed, "#timers-read")
    def timers_read(self) -> None:
        self.read_timers()

    @work(exclusive=True, group="timers")
    async def read_timers(self) -> None:
        await self._read_timers_now()

    async def _read_timers_now(self) -> None:
        if not self.ready():
            return
        view = self.query_one("#timer-view", Static)
        view.update("reading…")

        async def one(d: L.Device) -> str:
            c = self.group.clients[d]
            on_ = await L._query_timer(c, L.TIMER_ON)
            off = await L._query_timer(c, L.TIMER_OFF)
            return f"[b]{d.name}[/]   on: {L._timer_text(on_)}   off: {L._timer_text(off)}"

        live = [d for d in self.group.live if d.name not in self.group.muted]
        res = await asyncio.gather(*(one(d) for d in live), return_exceptions=True)
        view.update("\n".join(r if isinstance(r, str) else f"error: {r}" for r in res) or "no strips")

    @on(Select.Changed, "#timer-days")
    def timer_days(self, ev: Select.Changed) -> None:
        self.save(timer_days=ev.value)

    @on(Button.Pressed, "#timers-save")
    def timers_save(self) -> None:
        self.save_timers()

    @work(exclusive=True, group="timers")
    async def save_timers(self) -> None:
        if not self.ready():
            return
        days = L.parse_days(self.s["timer_days"])
        pkts = []
        for which, sel in ((L.TIMER_ON, "on"), (L.TIMER_OFF, "off")):
            text = self.query_one(f"#timer-{sel}-time", Input).value.strip()
            enabled = self.query_one(f"#timer-{sel}-enabled", Switch).value
            if not text:
                continue
            try:
                hm = L.parse_timer(text)
            except argparse.ArgumentTypeError as e:
                self.log_line(f"lightctl: {sel}-timer: {e}")
                return
            if hm == "off":
                hm, enabled = (0, 0), False
            pkts.append(L.cmd_timer(which, hm[0], hm[1], days, enabled))
        await self.write(L.cmd_clock(dt.datetime.now()), *pkts)
        self.log_line("timers saved" if pkts else "clock synced (no timer times entered)")
        await self._read_timers_now()

    @on(Button.Pressed, "#clock-sync")
    async def clock_sync(self) -> None:
        if self.ready():
            await self.write(L.cmd_clock(dt.datetime.now()))
            self.log_line("clock synced to the laptop")

    # ---------- tab 7: strips ----------

    @on(Switch.Changed, ".strip-switch")
    def strip_switch(self, ev: Switch.Changed) -> None:
        d = self.devices[int(ev.switch.id.removeprefix("strip-"))]
        if ev.value:
            self.group.muted.discard(d.name)
            self.sent.clear()
        else:
            self.group.muted.add(d.name)
        self.save(muted=sorted(self.group.muted))
        self.log_line(f"{d.name} {'included' if ev.value else 'left out'}")
        self.refresh_status()

    @on(Button.Pressed, "#reconnect")
    def reconnect_pressed(self) -> None:
        self.connect()


def run_ui(targets: str | None = None, order: str | None = None) -> None:
    LightUI(L.resolve_targets(targets, order)).run()


if __name__ == "__main__":
    run_ui()

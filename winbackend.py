"""Windows back-ends for lightctl: audio levels (WASAPI via `soundcard`) and screen capture
(`mss` + Pillow). The Linux equivalents (parec, the desktop portal + GStreamer) live in lightctl.py.

Imported only on Windows - and by the tests, which put stand-ins for the Windows libraries in
sys.modules. Everything here produces exactly what the Linux side does: audio levels on the app's
dB scale, and screen frames shrunk to w x h RGB bytes with a true area average.
"""
from __future__ import annotations

import asyncio
import contextlib
import ctypes
import math
import sys
import threading
import time

IS_WINDOWS = sys.platform == "win32"


def _need(module: str, pip_name: str):
    try:
        return __import__(module)
    except ImportError:
        raise SystemExit(f"lightctl: this needs the '{pip_name}' package. In the project folder run:\n"
                         f'  "{sys.executable}" -m pip install -r requirements.txt') from None


def _mss(mss_mod):
    """A screen grabber: mss.MSS (mss.mss() is deprecated since mss 10.2), or mss.mss() on older mss."""
    return (getattr(mss_mod, "MSS", None) or mss_mod.mss)()


def timer_resolution(enable: bool, ms: int = 1) -> bool:
    """Ask Windows for a 1 ms timer tick while streaming (the default is 15.6 ms), or give it back.
    asyncio's waits on Windows follow this tick, so it keeps the strips' pacing precise."""
    if not IS_WINDOWS:
        return False
    with contextlib.suppress(Exception):
        fn = ctypes.windll.winmm.timeBeginPeriod if enable else ctypes.windll.winmm.timeEndPeriod
        return fn(ms) == 0
    return False


# ---------- COM: soundcard talks to WASAPI through COM, which must be initialised per thread ----------

def _com_init() -> bool:
    if not IS_WINDOWS:
        return False
    hr = ctypes.windll.ole32.CoInitializeEx(None, 0)  # COINIT_MULTITHREADED
    return hr in (0, 1)  # S_OK / S_FALSE: balance with CoUninitialize (else: already set up otherwise)


def _com_done(initialised: bool) -> None:
    if initialised:
        ctypes.windll.ole32.CoUninitialize()


def _in_com_thread(fn, *args):
    """Run fn(*args) in a fresh thread with COM initialised, and return its result."""
    out: dict = {}

    def run() -> None:
        ok = _com_init()
        try:
            out["value"] = fn(*args)
        except BaseException as e:  # noqa: BLE001 - handed back to the caller
            out["error"] = e
        finally:
            _com_done(ok)

    t = threading.Thread(target=run, daemon=True)
    t.start()
    t.join()
    if "error" in out:
        raise out["error"]
    return out.get("value")


def _import_soundcard():
    """Import `soundcard` on a fresh thread without COM initialised yet.

    soundcard.mediafoundation creates a module-level _COMLibrary() that calls
    CoInitializeEx(..., COINIT_MULTITHREADED) and raises on S_FALSE (1). Calling _com_init()
    first on the same thread (or importing after WinRT/bleak already set COM on the main
    thread) therefore crashes with RuntimeError('Error 0x100000001').
    """
    if "soundcard" in sys.modules:
        return sys.modules["soundcard"]
    out: dict = {}

    def load() -> None:
        try:
            out["mod"] = _need("soundcard", "soundcard")
        except BaseException as e:  # noqa: BLE001 - handed back to the caller
            out["error"] = e

    t = threading.Thread(target=load, daemon=True)
    t.start()
    t.join()
    if "error" in out:
        raise out["error"]
    return out["mod"]


# ---------- audio ----------

def audio_sources() -> list[dict]:
    """Every audio input, same shape as on Linux: name (WASAPI id), desc, monitor.
    'monitor' inputs record what a speaker plays (WASAPI loopback)."""
    def run():
        sc = _import_soundcard()
        return [{"name": m.id, "desc": m.name, "monitor": bool(getattr(m, "isloopback", False))}
                for m in sc.all_microphones(include_loopback=True)]
    return _in_com_thread(run)


def default_audio() -> tuple[str | None, str | None]:
    """(id of the default speaker, whose loopback carries what is playing; id of the default mic)."""
    def run():
        sc = _import_soundcard()
        spk = mic = None
        with contextlib.suppress(Exception):
            spk = sc.default_speaker().id
        with contextlib.suppress(Exception):
            mic = sc.default_microphone().id
        return spk, mic
    return _in_com_thread(run)


class WinAudioInput:
    """Audio levels from WASAPI - what the speakers play (loopback) or a microphone - one dB value
    (the app's scale, as if the samples were 16-bit) per 1/fps seconds.

    soundcard's recorder blocks, so it runs in its own thread (with COM initialised) and hands
    levels to asyncio; only the newest few are kept, so a slow consumer never builds up lag.
    WASAPI delivers nothing during silence; soundcard then returns zeros, and level() treats a
    missing value as silence too.
    """

    RATE = 48000

    def __init__(self, device_id: str, loopback: bool, fps: float) -> None:
        self.device_id, self.loopback, self.fps = device_id, loopback, fps
        self.chunk = max(1, round(self.RATE / fps))
        self.stop_evt = threading.Event()
        self.thread: threading.Thread | None = None

    async def start(self) -> None:
        self.loop = asyncio.get_running_loop()
        self.queue: asyncio.Queue = asyncio.Queue(maxsize=4)
        ready = self.loop.create_future()
        self.thread = threading.Thread(target=self._run, args=(ready,), daemon=True)
        self.thread.start()
        await ready  # raises if the device couldn't be opened

    def _run(self, ready: asyncio.Future) -> None:
        def settle(exc: BaseException | None = None) -> None:
            if ready.done():
                if exc is not None:
                    self._put(exc)
            elif exc is None:
                ready.set_result(None)
            else:
                ready.set_exception(SystemExit(f"lightctl: cannot record from that audio input: {exc}"))

        ok = _com_init()
        try:
            sc = _import_soundcard()
            np = _need("numpy", "numpy")
            mic = sc.get_microphone(self.device_id, include_loopback=self.loopback)
            with mic.recorder(samplerate=self.RATE, channels=1, blocksize=self.chunk) as rec:
                self.loop.call_soon_threadsafe(settle)
                while not self.stop_evt.is_set():
                    data = rec.record(numframes=self.chunk)
                    ms = float(np.mean(np.square(data, dtype=np.float64))) if data.size else 0.0
                    db = 10 * math.log10(ms * 32768.0 ** 2) if ms > 0 else float("-inf")
                    self.loop.call_soon_threadsafe(self._put, db)
        except BaseException as e:  # noqa: BLE001 - reported through the queue / start()
            with contextlib.suppress(RuntimeError):  # loop already closed
                self.loop.call_soon_threadsafe(settle, e)
        finally:
            _com_done(ok)

    def _put(self, item) -> None:
        if self.queue.full():
            self.queue.get_nowait()  # keep the newest: lag must not build up
        self.queue.put_nowait(item)

    async def level(self) -> float:
        try:
            item = await asyncio.wait_for(self.queue.get(), timeout=max(1.0, 3 / self.fps))
        except asyncio.TimeoutError:
            return float("-inf")  # nothing arrived: silence
        if isinstance(item, BaseException):
            raise SystemExit(f"lightctl: audio capture stopped: {item}")
        return item

    async def stop(self) -> None:
        self.stop_evt.set()
        if self.thread is not None:
            await asyncio.to_thread(self.thread.join, 2)


# ---------- windows and monitors (Win32 via ctypes) ----------

def _user32():
    from ctypes import wintypes
    u = ctypes.windll.user32
    hwnd = wintypes.HWND
    u.IsWindow.argtypes = [hwnd]
    u.IsWindowVisible.argtypes = [hwnd]
    u.IsIconic.argtypes = [hwnd]
    u.GetWindowTextLengthW.argtypes = [hwnd]
    u.GetWindowTextW.argtypes = [hwnd, wintypes.LPWSTR, ctypes.c_int]
    u.GetWindowRect.argtypes = [hwnd, ctypes.POINTER(wintypes.RECT)]
    u.GetForegroundWindow.restype = hwnd
    return u


def _dpi_aware() -> None:
    """Real pixel coordinates even with display scaling (mss does the same when it starts)."""
    with contextlib.suppress(Exception):
        ctypes.windll.shcore.SetProcessDpiAwareness(2)  # per-monitor; fails harmlessly if already set


def window_title(hwnd) -> str:
    u = _user32()
    n = u.GetWindowTextLengthW(hwnd)
    buf = ctypes.create_unicode_buffer(n + 1)
    u.GetWindowTextW(hwnd, buf, n + 1)
    return buf.value


def window_rect(hwnd) -> tuple[int, int, int, int] | None:
    """(left, top, right, bottom) of the visible window frame, or None if it is gone or minimised."""
    from ctypes import wintypes
    u = _user32()
    if not u.IsWindow(hwnd) or u.IsIconic(hwnd):
        return None
    r = wintypes.RECT()
    dwm = ctypes.windll.dwmapi
    # DWMWA_EXTENDED_FRAME_BOUNDS = 9: without the invisible resize borders
    if dwm.DwmGetWindowAttribute(wintypes.HWND(hwnd), 9, ctypes.byref(r), ctypes.sizeof(r)) != 0:
        if not u.GetWindowRect(hwnd, ctypes.byref(r)):
            return None
    return r.left, r.top, r.right, r.bottom


def list_windows() -> list[dict]:
    """Visible top-level windows with a title: hwnd, title, rect."""
    from ctypes import wintypes
    _dpi_aware()
    u = _user32()
    out: list[dict] = []

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def each(hwnd, _lparam):
        if not hwnd or not u.IsWindowVisible(hwnd) or u.IsIconic(hwnd) or not u.GetWindowTextLengthW(hwnd):
            return True
        cloaked = ctypes.c_int(0)  # DWMWA_CLOAKED = 14: hidden UWP / other-desktop windows
        ctypes.windll.dwmapi.DwmGetWindowAttribute(wintypes.HWND(hwnd), 14, ctypes.byref(cloaked),
                                                   ctypes.sizeof(cloaked))
        rect = window_rect(hwnd)
        if not cloaked.value and rect and rect[2] - rect[0] >= 100 and rect[3] - rect[1] >= 100:
            out.append({"hwnd": hwnd, "title": window_title(hwnd), "rect": rect})
        return True

    u.EnumWindows(each, 0)
    return out


def list_monitors() -> list[dict]:
    """Monitors as mss numbers them (1, 2, ...), with which one is the primary."""
    mss = _need("mss", "mss")
    with _mss(mss) as sct:
        mons = list(sct.monitors[1:])
    primary = next((i for i, m in enumerate(mons) if m.get("is_primary")), None)
    if primary is None:  # older mss: the primary monitor sits at (0, 0)
        primary = next((i for i, m in enumerate(mons) if (m["left"], m["top"]) == (0, 0)), 0)
    return [{"index": i + 1, "width": m["width"], "height": m["height"], "left": m["left"], "top": m["top"],
             "primary": i == primary, "name": m.get("name") or ""} for i, m in enumerate(mons)]


# ---------- screen capture ----------

class WinScreenCapture:
    """Screen frames on Windows: mss grabs a monitor (or the area of one window) and Pillow shrinks
    it with a box filter - every output pixel is the true average of its screen area - to w x h RGB.

    target: "monitor" (the primary), "monitor:N", "window:TITLE" (first window whose title contains
    TITLE), or "window" (the next window you click). A window is captured as it appears on screen,
    so keep it uncovered; while it is minimised the frames are black.
    Grabbing runs in its own thread; frame() returns the newest frame.
    """

    def __init__(self, fps: float, verbose: bool, target: str, w: int, h: int, log=print) -> None:
        self.fps, self.verbose, self.target, self.w, self.h, self.log = max(1.0, fps), verbose, target, w, h, log
        self.stop_evt = threading.Event()
        self.thread: threading.Thread | None = None
        self.hwnd = None
        self.monitor_index: int | None = None

    async def __aenter__(self) -> "WinScreenCapture":
        _need("mss", "mss")
        _need("PIL", "Pillow")
        self.loop = asyncio.get_running_loop()
        self.queue: asyncio.Queue = asyncio.Queue(maxsize=1)
        kind, _, arg = self.target.partition(":")
        if kind == "window":
            self.hwnd = await self._pick_window(arg.strip())
        else:
            self.monitor_index = int(arg) if arg.strip().isdigit() else None
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()
        return self

    async def _pick_window(self, title: str):
        if title:
            wins = [w for w in list_windows() if title.lower() in w["title"].lower()]
            if not wins:
                raise SystemExit(f"lightctl: no open window has {title!r} in its title "
                                 "(see lightctl --list-windows)")
            self.log(f"following window: {wins[0]['title']}")
            return wins[0]["hwnd"]
        u = _user32()
        own = u.GetForegroundWindow()
        self.log("Click the window to follow (e.g. the video player) within 10 seconds...")
        for _ in range(100):
            await asyncio.sleep(0.1)
            fg = u.GetForegroundWindow()
            if fg and fg != own:
                self.log(f"following window: {window_title(fg)}")
                return fg
        raise SystemExit("lightctl: no window was clicked; try --capture window:TITLE")

    def _region(self, sct, mon: dict) -> dict | None:
        if self.hwnd is None:
            return mon
        rect = window_rect(self.hwnd)
        if rect is None:
            if not _user32().IsWindow(self.hwnd):
                raise RuntimeError("the captured window was closed")
            return None  # minimised
        desk = sct.monitors[0]
        left, top = max(rect[0], desk["left"]), max(rect[1], desk["top"])
        right = min(rect[2], desk["left"] + desk["width"])
        bottom = min(rect[3], desk["top"] + desk["height"])
        if right - left < 8 or bottom - top < 8:
            return None
        return {"left": left, "top": top, "width": right - left, "height": bottom - top}

    def _run(self) -> None:
        try:
            mss = _need("mss", "mss")
            from PIL import Image
            with _mss(mss) as sct:  # also makes the process per-monitor DPI aware
                mons = sct.monitors
                if self.monitor_index and 1 <= self.monitor_index < len(mons):
                    mon = mons[self.monitor_index]
                else:
                    primary = [m for m in mons[1:] if m.get("is_primary")]
                    at_origin = [m for m in mons[1:] if (m["left"], m["top"]) == (0, 0)]
                    mon = (primary or at_origin or mons[1:2] or mons)[0]
                black = bytes(self.w * self.h * 3)
                while not self.stop_evt.is_set():
                    t = time.monotonic()
                    region = self._region(sct, mon)
                    if region is None:
                        frame = black
                    else:
                        shot = sct.grab(region)
                        img = Image.frombytes("RGB", tuple(shot.size), shot.bgra, "raw", "BGRX")
                        frame = img.resize((self.w, self.h), Image.Resampling.BOX).tobytes()
                    self.loop.call_soon_threadsafe(self._put, frame)
                    self.stop_evt.wait(max(0.0, 1 / self.fps - (time.monotonic() - t)))
        except BaseException as e:  # noqa: BLE001 - reported by frame()
            with contextlib.suppress(RuntimeError):
                self.loop.call_soon_threadsafe(self._put, e)

    def _put(self, item) -> None:
        if self.queue.full():
            self.queue.get_nowait()
        self.queue.put_nowait(item)

    async def frame(self) -> bytes:
        item = await self.queue.get()
        if isinstance(item, BaseException):
            raise SystemExit(f"lightctl: screen capture stopped: {item}")
        return item

    async def __aexit__(self, *exc) -> None:
        self.stop_evt.set()
        if self.thread is not None:
            await asyncio.to_thread(self.thread.join, 2)

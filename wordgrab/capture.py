from __future__ import annotations

import ctypes
import os
import threading
import time
from ctypes import wintypes

from . import db
from .config import load_config
from .logsetup import get_logger, log_exception

user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
kernel32.GetCurrentThreadId.restype = wintypes.DWORD

WH_MOUSE_LL = 14
WM_LBUTTONDOWN = 0x0201
WM_LBUTTONUP = 0x0202
WM_QUIT = 0x0012
WM_KEYDOWN = 0x0100
WM_SYSKEYDOWN = 0x0101

VK_CONTROL = 0x11
VK_MENU = 0x12
VK_SHIFT = 0x10
VK_LWIN = 0x5B
VK_RWIN = 0x5C
VK_C = 0x43

GETTEXTEXTRAGLEN = 1024


class POINT(ctypes.Structure):
    _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]


class MSLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [
        ("pt", POINT),
        ("mouseData", wintypes.DWORD),
        ("flags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ctypes.POINTER(wintypes.ULONG)),
    ]


class RECT(ctypes.Structure):
    _fields_ = [
        ("l", ctypes.c_long),
        ("t", ctypes.c_long),
        ("r", ctypes.c_long),
        ("b", ctypes.c_long),
    ]


class GUITHREADINFO(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.DWORD),
        ("flags", wintypes.DWORD),
        ("hwndActive", wintypes.HWND),
        ("hwndFocus", wintypes.HWND),
        ("hwndCapture", wintypes.HWND),
        ("hwndMenuOwner", wintypes.HWND),
        ("hwndMoveSize", wintypes.HWND),
        ("hwndCaret", wintypes.HWND),
        ("rcCaret", RECT),
    ]


HOOKPROC = ctypes.WINFUNCTYPE(
    ctypes.c_ssize_t, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM
)

user32.SetWindowsHookExW.argtypes = [ctypes.c_int, HOOKPROC, ctypes.c_void_p, wintypes.DWORD]
user32.SetWindowsHookExW.restype = wintypes.HHOOK
user32.CallNextHookEx.argtypes = [wintypes.HHOOK, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM]
user32.CallNextHookEx.restype = ctypes.c_ssize_t
user32.UnhookWindowsHookEx.argtypes = [wintypes.HHOOK]
user32.GetGUIThreadInfo.argtypes = [wintypes.DWORD, ctypes.POINTER(GUITHREADINFO)]
user32.GetGUIThreadInfo.restype = wintypes.BOOL
user32.GetForegroundWindow.restype = wintypes.HWND
user32.GetWindowTextLengthW.argtypes = [wintypes.HWND]
user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
user32.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
user32.GetWindowThreadProcessId.restype = wintypes.DWORD
user32.WindowFromPoint.argtypes = [POINT]
user32.WindowFromPoint.restype = wintypes.HWND
user32.PostThreadMessageW.argtypes = [wintypes.DWORD, ctypes.c_uint, wintypes.WPARAM, wintypes.LPARAM]
user32.GetMessageW.argtypes = [ctypes.POINTER(wintypes.MSG), wintypes.HWND, ctypes.c_uint, ctypes.c_uint]
user32.TranslateMessage.argtypes = [ctypes.POINTER(wintypes.MSG)]
user32.DispatchMessageW.argtypes = [ctypes.POINTER(wintypes.MSG)]
user32.keybd_event.argtypes = [wintypes.BYTE, wintypes.BYTE, wintypes.DWORD, ctypes.c_ulonglong]
user32.GetAsyncKeyState.argtypes = [ctypes.c_int]


DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2 = -4
DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE = -3


def enable_dpi_awareness() -> str:
    """Opt into per-monitor DPI awareness so mouse coordinates from the hook
    match the coordinates every other API reports (matters on scaled displays)."""
    try:
        user32.SetProcessDpiAwarenessContext.argtypes = [ctypes.c_void_p]
        if user32.SetProcessDpiAwarenessContext(
            ctypes.c_void_p(DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2)
        ):
            return "per-monitor-v2"
    except Exception:
        pass
    try:
        ctypes.WinDLL("shcore").SetProcessDpiAwareness(2)
        return "per-monitor"
    except Exception:
        pass
    try:
        user32.SetProcessDPIAware()
        return "system"
    except Exception:
        return "none"


def get_foreground_window() -> int:
    return int(user32.GetForegroundWindow())


def get_window_title(hwnd: int) -> str:
    if not hwnd:
        return ""
    length = user32.GetWindowTextLengthW(hwnd)
    if length <= 0:
        return ""
    buf = ctypes.create_unicode_buffer(length + 1)
    user32.GetWindowTextW(hwnd, buf, length + 1)
    return buf.value


def get_class_name(hwnd: int) -> str:
    if not hwnd:
        return ""
    buf = ctypes.create_unicode_buffer(256)
    user32.GetClassNameW(hwnd, buf, 256)
    return buf.value


def get_window_thread_id(hwnd: int) -> int:
    return int(user32.GetWindowThreadProcessId(wintypes.HWND(hwnd), None))


def get_window_pid(hwnd: int) -> int:
    pid = wintypes.DWORD()
    user32.GetWindowThreadProcessId(wintypes.HWND(hwnd), ctypes.byref(pid))
    return int(pid.value)


def key_down(vk: int) -> None:
    user32.keybd_event(vk, 0, 0, 0)


def key_up(vk: int) -> None:
    user32.keybd_event(vk, 0, 2, 0)


def is_modifier_pressed() -> bool:
    return bool(user32.GetAsyncKeyState(VK_CONTROL) & 0x8000) or bool(
        user32.GetAsyncKeyState(VK_MENU) & 0x8000
    )


def _clipboard_api():
    global _clipboard
    if _clipboard is None:
        import win32clipboard

        _clipboard = win32clipboard
    return _clipboard


class ClipboardBusy(Exception):
    """The clipboard is locked by another process and we must not touch it."""


class Clipboard:
    """Send Ctrl+C, read CF_UNICODETEXT, then restore the previous clipboard text.

    Another process can hold the clipboard open at any moment, so every access
    is retried and, importantly, nothing is ever cleared unless we are sure we
    can put the old contents back.
    """

    RETRY_SECONDS = 2.0

    def __init__(self) -> None:
        self._api = _clipboard_api()
        self._previous: str | None = None
        self._had_text = False
        self._cleared = False

    def _open(self, deadline: float) -> bool:
        while time.time() < deadline:
            try:
                self._api.OpenClipboard()
                return True
            except Exception:
                time.sleep(0.03)
        return False

    def _read(self) -> str | None:
        if not self._open(time.time() + 0.15):
            return None
        try:
            if self._api.IsClipboardFormatAvailable(self._api.CF_UNICODETEXT):
                return str(self._api.GetClipboardData(self._api.CF_UNICODETEXT))
            return None
        except Exception:
            return None
        finally:
            try:
                self._api.CloseClipboard()
            except Exception:
                pass

    def _write(self, text: str | None) -> bool:
        deadline = time.time() + self.RETRY_SECONDS
        while True:
            if not self._open(deadline):
                return False
            try:
                self._api.EmptyClipboard()
                self._cleared = True
                if text is not None:
                    self._api.SetClipboardData(self._api.CF_UNICODETEXT, text)
                return True
            except Exception:
                time.sleep(0.03)
            finally:
                try:
                    self._api.CloseClipboard()
                except Exception:
                    pass
            if time.time() >= deadline:
                return False

    def snapshot(self) -> None:
        self._previous = None
        self._had_text = False
        for _ in range(3):
            text = self._read()
            if text is not None:
                self._previous = text
                self._had_text = True
                return
            time.sleep(0.05)

    def copy_from_foreground(self, timeout_ms: int = 600) -> str | None:
        """Clear the clipboard, press Ctrl+C, then read whatever the app copied."""
        if not self._write(None):  # busy: don't risk the user's clipboard
            raise ClipboardBusy("clipboard locked, cannot take a fresh snapshot")
        key_down(VK_CONTROL)
        key_down(VK_C)
        key_up(VK_C)
        key_up(VK_CONTROL)
        deadline = time.time() + timeout_ms / 1000
        while time.time() < deadline:
            text = self._read()
            if text:
                return text
            time.sleep(0.03)
        return None

    def restore(self) -> None:
        """Put the previous text back. Retries hard: losing it would be data loss."""
        if not self._cleared:
            return
        if self._had_text and self._previous is not None:
            if self._write(self._previous):
                self._cleared = False
                return
            get_logger().error("clipboard restore failed; previous text was %d chars",
                               len(self._previous))
        else:
            # there was no text to begin with; leave it empty as we found it
            self._write(None)
        self._cleared = False


_uia_module = None
_uia_lock = threading.Lock()
_tls = threading.local()
_clipboard = None


def _uia():
    """Lazily build the UIAutomation client; COM objects are cached per thread."""
    global _uia_module
    with _uia_lock:
        if _uia_module is None:
            import comtypes.client as cc

            last: Exception | None = None
            for dll in ("UIAutomationCore.dll", "UIAutomationClient.dll"):
                try:
                    _uia_module = cc.GetModule(dll)
                    break
                except Exception as exc:
                    last = exc
            if _uia_module is None:
                raise RuntimeError(f"UIA module unavailable: {last}")
    auto = getattr(_tls, "auto", None)
    if auto is None:
        import comtypes.client as cc

        auto = cc.CreateObject(_uia_module.CUIAutomation,
                               interface=_uia_module.IUIAutomation)
        _tls.auto = auto
    return _uia_module, auto


def get_selection_via_uia() -> tuple[str, str]:
    """Return (selected_text, document_text) from UI Automation TextPattern.

    Best effort: many plain Win32 controls do not implement TextPattern, in which
    case the selection comes back empty and the caller falls back to the clipboard.
    """
    try:
        UIA, auto = _uia()
    except Exception:
        return "", ""

    try:
        import comtypes

        comtypes.CoInitialize()
    except Exception:
        pass
    try:
        hwnd = get_foreground_window()
        if not hwnd:
            return "", ""
        elements = []
        for getter in ("GetFocusedElement", "ElementFromHandle"):
            try:
                elem = getattr(auto, getter)(hwnd) if getter == "ElementFromHandle" \
                    else auto.GetFocusedElement()
                if elem is not None:
                    elements.append(elem)
            except Exception:
                continue

        selection = ""
        context = ""
        for elem in elements:
            for name in ("UIA_TextPatternId", "UIA_TextPattern2Id"):
                pid = getattr(UIA, name, None)
                if not pid:
                    continue
                try:
                    pattern = elem.GetCurrentPattern(pid)
                except Exception:
                    continue
                if pattern is None:
                    continue
                if not selection:
                    try:
                        ranges = pattern.GetSelection()
                        if ranges and ranges[0]:
                            selection = ranges[0].GetText(-1)
                    except Exception:
                        selection = ""
                if not context:
                    try:
                        context = pattern.DocumentRange.GetText(-1)
                    except Exception:
                        context = ""
                if selection:
                    break
            if selection:
                break
        return selection.strip(), context
    except Exception:
        return "", ""
    finally:
        try:
            import comtypes

            comtypes.CoUninitialize()
        except Exception:
            pass


def get_selection_via_clipboard() -> tuple[str, str]:
    cb = Clipboard()
    cb.snapshot()
    try:
        return (cb.copy_from_foreground() or ""), ""
    except ClipboardBusy:
        return "", ""
    finally:
        cb.restore()


def window_from_point(x: int, y: int) -> int:
    return int(user32.WindowFromPoint(POINT(x, y)))


def get_caret_info() -> tuple[int, int]:
    hwnd = get_foreground_window()
    tid = get_window_thread_id(hwnd)
    info = GUITHREADINFO()
    info.cbSize = ctypes.sizeof(GUITHREADINFO)
    if not user32.GetGUIThreadInfo(tid, ctypes.byref(info)):
        return 0, 0
    return info.rcCaret.l, info.rcCaret.t


class DoubleClickWatcher:
    """Low-level mouse hook that reports double-clicks outside our own windows."""

    def __init__(self, on_double_click) -> None:
        self._on_double_click = on_double_click
        self._hook_proc = HOOKPROC(self._mouse_event)
        self._hook_id = None
        self._thread_id = None
        self._stop = threading.Event()
        self._last_down: tuple[int, int, float] | None = None
        self._last_fire = 0.0
        self._suppress = 0.0
        cfg = load_config()
        self._interval = float(cfg["double_click_interval_ms"]) / 1000.0
        self._excluded = {c.lower() for c in cfg.get("ignore_apps", [])}

    def suppress(self, seconds: float = 0.8) -> None:
        self._suppress = time.time() + seconds

    def _mouse_event(self, code: int, wparam: int, lparam: int) -> int:
        if code >= 0:
            try:
                if wparam == WM_LBUTTONDOWN:
                    self._handle_down(lparam)
            except Exception:
                pass
        return int(user32.CallNextHookEx(self._hook_id, code, wparam, lparam))

    def _handle_down(self, lparam: int) -> None:
        if time.time() < self._suppress:
            return
        info = MSLLHOOKSTRUCT()
        ctypes.memmove(ctypes.byref(info), ctypes.c_void_p(lparam), ctypes.sizeof(info))
        now = time.time()
        if self._last_down:
            lx, ly, lt = self._last_down
            if (now - lt <= self._interval
                    and abs(info.pt.x - lx) <= 6 and abs(info.pt.y - ly) <= 6):
                self._last_down = None
                x, y = info.pt.x, info.pt.y
                threading.Thread(
                    target=self._fire, args=(x, y), name="dblclick", daemon=True
                ).start()
            else:
                self._last_down = (info.pt.x, info.pt.y, now)
        else:
            self._last_down = (info.pt.x, info.pt.y, now)

    def _fire(self, x: int, y: int, hwnd: int | None = None,
              title: str | None = None, cls: str | None = None) -> None:
        time.sleep(0.06)  # let the app finish its own selection
        now = time.time()
        if now - self._last_fire < 0.4:
            return  # a capture for this click is already in flight
        if now < self._suppress:
            return
        if hwnd is None:
            hwnd = get_foreground_window()
        if not hwnd or get_window_pid(hwnd) == os.getpid():
            return  # our own windows
        if is_modifier_pressed():
            return
        if cls is None:
            cls = get_class_name(hwnd).lower()
        if title is None:
            title = get_window_title(hwnd)
        for bad in self._excluded:
            if bad.lower() in cls or bad.lower() in title.lower():
                return
        self._last_fire = now
        try:
            self._on_double_click(x, y, hwnd, title, cls)
        except Exception:
            log_exception("double-click capture")

    def start(self) -> None:
        if self._hook_id:
            return
        enable_dpi_awareness()

        def _run() -> None:
            self._thread_id = kernel32.GetCurrentThreadId()
            self._hook_id = user32.SetWindowsHookExW(
                WH_MOUSE_LL, self._hook_proc, None, 0
            )
            if not self._hook_id:
                return
            msg = wintypes.MSG()
            while not self._stop.is_set():
                result = user32.GetMessageW(ctypes.byref(msg), None, 0, 0)
                if result in (0, -1):
                    break
                user32.TranslateMessage(ctypes.byref(msg))
                user32.DispatchMessageW(ctypes.byref(msg))

        self._thread = threading.Thread(target=_run, name="mouse-hook", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._hook_id:
            if self._thread_id:
                user32.PostThreadMessageW(self._thread_id, WM_QUIT, 0, 0)
            else:
                user32.UnhookWindowsHookEx(self._hook_id)


def capture_word(x: int, y: int) -> dict | None:
    """Grab the word under (x, y). Never raises.

    Order: UI Automation selection (silent, best for rich controls) -> Ctrl+C
    (works everywhere, clipboard is saved and restored).
    """
    try:
        cfg = load_config()
        text, context = get_selection_via_uia()
        method = "uia"
        if not text.strip():
            text, _ = get_selection_via_clipboard()
            method = "clipboard"
        if not context:
            _, context = get_selection_via_uia()
        text = (text or "").strip()
        if not text:
            return None
        if len(text) > 200 and "\n" not in text:
            text = text[:200]
        elif "\n" in text:
            text = max(text.splitlines(), key=len).strip()
        word = db.normalize_word(text)
        if not word:
            return None
        if len(word) < int(cfg["min_word_len"]) or len(word) > int(cfg["max_word_len"]):
            return None
        sentence = db.sentence_around(context, word) if context else ""
        return {"word": word, "raw": text, "sentence": sentence, "method": method}
    except Exception:
        log_exception("capture_word")
        return None
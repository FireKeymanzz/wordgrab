from __future__ import annotations

import ctypes
import queue
import threading
import winreg
from ctypes import wintypes

from .config import APP_NAME, load_config
from .logsetup import get_logger

RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
WM_HOTKEY = 0x0312
MOD_NOREPEAT = 0x4000

NAMED_MODS = {
    "ctrl": 0x0002, "control": 0x0002, "alt": 0x0001, "altgr": 0x0001,
    "shift": 0x0004, "win": 0x0008, "super": 0x0008, "meta": 0x0008,
}
NAMED_KEYS = {
    **{f"f{i}": 0x6F + i for i in range(1, 13)},
    "space": 0x20, "spacebar": 0x20, "enter": 0x0D, "return": 0x0D,
    "esc": 0x1B, "escape": 0x1B, "tab": 0x09, "backspace": 0x08,
    "`": 0xC0, "-": 0xBD, "=": 0xBB, "[": 0xDB, "]": 0xDD,
    "\\": 0xDC, ";": 0xBA, "'": 0xDE, ",": 0xBC, ".": 0xBE, "/": 0xBF,
}

user32 = ctypes.WinDLL("user32", use_last_error=True)


def is_autostart_enabled() -> bool:
    import sys

    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
            value, _ = winreg.QueryValueEx(key, APP_NAME)
            return value.startswith(f'"{sys.executable}"')
    except OSError:
        return False


def set_autostart(enabled: bool) -> None:
    import sys

    with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as key:
        if enabled:
            cmd = f'"{sys.executable}" -m wordgrab --minimized'
            winreg.SetValueEx(key, APP_NAME, 0, winreg.REG_SZ, cmd)
        else:
            try:
                winreg.DeleteValue(key, APP_NAME)
            except OSError:
                pass


def parse_hotkey(spec: str) -> tuple[int, int]:
    parts = [p.strip().lower() for p in (spec or "").split("+") if p.strip()]
    mods = 0
    key = 0
    for p in parts:
        if p in NAMED_MODS:
            mods |= NAMED_MODS[p]
        elif p in NAMED_KEYS:
            key = NAMED_KEYS[p]
        elif len(p) == 1:
            key = ord(p.upper())
    return mods, key


def _key_label(key: int) -> str:
    if 0x70 <= key <= 0x87:
        return f"F{key - 0x6F}"
    if 0x30 <= key <= 0x5A:
        return chr(key)
    return {0x20: "space", 0x0D: "enter", 0x1B: "esc"}.get(key, hex(key))


class HotkeyManager:
    """Global hotkeys on a dedicated thread with its own message pump."""

    def __init__(self) -> None:
        self._queue: "queue.Queue[tuple[int, int, int, object]]" = queue.Queue()
        self._callbacks: dict[int, object] = {}
        self._next_id = 1
        self._stop = threading.Event()
        self._ready = threading.Event()
        self._thread: threading.Thread | None = None

    def register(self, spec: str, callback) -> bool:
        """Register a hotkey, falling back to alternates if the app is taken."""
        mods, key = parse_hotkey(spec)
        if not key:
            get_logger().warning("cannot parse hotkey %r", spec)
            return False
        key_label = _key_label(key)
        base_mods = mods
        attempts = [
            (base_mods, key, spec),
            (base_mods | 0x0004, key, f"ctrl+alt+shift+{key_label.lower()}"),
            (base_mods, 0x78, f"ctrl+alt+F9 (原 {spec} 被占用)"),
            (base_mods, 0x79, f"ctrl+alt+F10 (原 {spec} 被占用)"),
        ]
        hid = self._next_id
        self._next_id += 1
        self._callbacks[hid] = callback
        self._queue.put((hid, MOD_NOREPEAT, spec, attempts))
        return True

    def _pump(self) -> None:
        msg = wintypes.MSG()
        pending = list(self._queue.queue)
        self._queue.queue.clear()
        for hid, _, spec, attempts in pending:
            self._register_with_fallback(hid, attempts, spec)
        while not self._stop.is_set():
            while True:
                try:
                    item = self._queue.get_nowait()
                except queue.Empty:
                    break
                if item[0] is None:
                    self._stop.set()
                    continue
                self._register_with_fallback(item[0], item[3], item[2])
            result = user32.GetMessageW(ctypes.byref(msg), None, 0, 0)
            if result in (0, -1):
                break
            if msg.message == WM_HOTKEY:
                cb = self._callbacks.get(int(msg.wParam))
                get_logger().info("hotkey id=%s fired (%s)",
                                  msg.wParam, "ok" if cb else "no callback")
                if cb is not None:
                    threading.Thread(target=cb, name="hotkey", daemon=True).start()
                else:
                    get_logger().warning("hotkey %s fired with no callback", msg.wParam)

    def _register_with_fallback(self, hid: int, attempts, spec: str) -> bool:
        for mods, key, label in attempts:
            ctypes.set_last_error(0)
            if user32.RegisterHotKey(None, hid, mods | MOD_NOREPEAT, key):
                get_logger().info("hotkey %s registered (id=%d)", label, hid)
                return True
        err = ctypes.get_last_error()
        get_logger().error(
            "no free hotkey for %s (winerr=%d) — 该功能只能用托盘菜单触发", spec, err)
        self._callbacks.pop(hid, None)
        return False

    def start(self) -> None:
        if self._thread:
            return
        self._thread = threading.Thread(target=self._pump, name="hotkeys", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._queue.put((None, 0, 0, None))
        self._stop.set()

    def unregister_all(self) -> None:
        for hid in list(self._callbacks):
            user32.UnregisterHotKey(None, hid)
            self._callbacks.pop(hid, None)


def register_defaults(hotkeys: HotkeyManager) -> HotkeyManager:
    cfg = load_config()
    specs = {
        "hotkey_panel": cfg.get("hotkey_panel", "ctrl+alt+w"),
        "hotkey_review": cfg.get("hotkey_review", "ctrl+alt+r"),
        "hotkey_quick_add": cfg.get("hotkey_quick_add", "ctrl+alt+d"),
    }
    # callbacks are attached by main.py
    for name, spec in specs.items():
        hotkeys.register(spec, name)
    return hotkeys
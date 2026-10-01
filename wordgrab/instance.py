"""Single-instance guard.

Without this, every double-click on start.bat spawns another tray icon and
another copy of the app. They fight over the API port, only the first one keeps
the Ctrl+Alt+W hotkey, and the user ends up clicking a tray icon that looks
dead. Here a second launch just asks the running instance to show its panel.
"""

from __future__ import annotations

import ctypes
from ctypes import wintypes

from .logsetup import get_logger

kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
user32 = ctypes.WinDLL("user32", use_last_error=True)

MUTEX_NAME = "Local\\WordGrabSingleInstance"
WINDOW_CLASS = "WordGrabSingleInstanceWindow"
WINDOW_TITLE = "WordGrabSingleInstanceHost"
WM_SHOW_PANEL = 0x8001  # WM_APP + 1
WM_APP = 0x8000

ERROR_ALREADY_EXISTS = 183

WNDCLASSW_FIELDS = [
    ("style", ctypes.c_uint),
    ("lpfnWndProc", ctypes.c_void_p),
    ("cbClsExtra", ctypes.c_int),
    ("cbWndExtra", ctypes.c_int),
    ("hInstance", wintypes.HINSTANCE),
    ("hIcon", wintypes.HICON),
    ("hCursor", wintypes.HANDLE),
    ("hbrBackground", wintypes.HBRUSH),
    ("lpszMenuName", wintypes.LPCWSTR),
    ("lpszClassName", wintypes.LPCWSTR),
]
WNDCLASSW = type("WNDCLASSW", (ctypes.Structure,), {"_fields_": WNDCLASSW_FIELDS})
WNDPROC = ctypes.WINFUNCTYPE(ctypes.c_ssize_t, wintypes.HWND, ctypes.c_uint,
                             wintypes.WPARAM, wintypes.LPARAM)

kernel32.CreateMutexW.argtypes = [ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR]
kernel32.CreateMutexW.restype = wintypes.HANDLE
kernel32.CreateMutexW.set_last_error = True  # type: ignore[attr-defined]
user32.FindWindowW.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR]
user32.FindWindowW.restype = wintypes.HWND
user32.PostMessageW.argtypes = [wintypes.HWND, ctypes.c_uint, wintypes.WPARAM, wintypes.LPARAM]
user32.CreateWindowExW.restype = wintypes.HWND
user32.CreateWindowExW.argtypes = [
    wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD,
    ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
    wintypes.HWND, wintypes.HMENU, wintypes.HINSTANCE, ctypes.c_void_p]
user32.RegisterClassW.argtypes = [ctypes.c_void_p]
user32.DefWindowProcW.restype = ctypes.c_ssize_t
user32.DefWindowProcW.argtypes = [wintypes.HWND, ctypes.c_uint, wintypes.WPARAM, wintypes.LPARAM]
user32.DestroyWindow.argtypes = [wintypes.HWND]

_lock = None


def is_running() -> bool:
    """True when another instance already holds the mutex."""
    handle = kernel32.CreateMutexW(None, False, MUTEX_NAME)
    if not handle:
        return False
    already = ctypes.get_last_error() == ERROR_ALREADY_EXISTS
    if not already:
        kernel32.CloseHandle(handle)
    return already


def request_show_panel() -> bool:
    """Ask the running instance to open its panel. True if it was reached."""
    hwnd = user32.FindWindowW(WINDOW_CLASS, WINDOW_TITLE)
    if not hwnd:
        return False
    user32.PostMessageW(hwnd, WM_SHOW_PANEL, 0, 0)
    return True


def hold(on_message) -> tuple[int, int]:
    """Take the mutex and publish a hidden window that listens for WM_SHOW_PANEL.

    Returns (mutex_handle, thread_id) for diagnostics.
    """
    global _lock
    handle = kernel32.CreateMutexW(None, False, MUTEX_NAME)
    thread_id = int(kernel32.GetCurrentThreadId())

    def wndproc(hwnd, msg, wparam, lparam):
        if msg == WM_SHOW_PANEL:
            get_logger().info("second launch asked us to show the panel")
            try:
                on_message()
            except Exception:
                get_logger().exception("panel show request")
            return 0
        return user32.DefWindowProcW(hwnd, msg, wparam, lparam)

    proc = WNDPROC(wndproc)
    wc = WNDCLASSW()
    wc.lpfnWndProc = ctypes.cast(proc, ctypes.c_void_p)
    wc.hInstance = kernel32.GetModuleHandleW(None)
    wc.lpszClassName = WINDOW_CLASS
    if user32.RegisterClassW(ctypes.byref(wc)):
        user32.CreateWindowExW(0, WINDOW_CLASS, WINDOW_TITLE, 0,
                               0, 0, 0, 0, None, None, wc.hInstance, None)
    _lock = (handle, proc)  # keep references alive
    return handle, thread_id
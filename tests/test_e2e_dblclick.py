"""End-to-end: synthesize a real double click over a word in another process and
verify the whole pipeline (hook -> capture -> HTTP API -> SQLite)."""

from __future__ import annotations

import ctypes
import json
import os
import subprocess
import sys
import time
import urllib.request
from ctypes import wintypes

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

from sandbox import activate, verify  # noqa: E402

TMP = activate("wordgrab_e2e_")

from wordgrab import capture, server  # noqa: E402

user32 = ctypes.WinDLL("user32", use_last_error=True)
user32.SetCursorPos.argtypes = [ctypes.c_int, ctypes.c_int]
user32.ClientToScreen.argtypes = [wintypes.HWND, ctypes.POINTER(capture.POINT)]
user32.GetWindow.argtypes = [wintypes.HWND, ctypes.c_uint]
user32.GetWindow.restype = wintypes.HWND
user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
user32.GetWindowThreadProcessId.restype = wintypes.DWORD
user32.GetForegroundWindow.restype = wintypes.HWND
user32.SetForegroundWindow.argtypes = [wintypes.HWND]
user32.BringWindowToTop.argtypes = [wintypes.HWND]
user32.AttachThreadInput.argtypes = [wintypes.DWORD, wintypes.DWORD, ctypes.c_int]
user32.SendMessageW.restype = ctypes.c_ssize_t
user32.SendMessageW.argtypes = [wintypes.HWND, ctypes.c_uint, ctypes.c_size_t, ctypes.c_ssize_t]
user32.mouse_event.argtypes = [wintypes.DWORD, ctypes.c_int, ctypes.c_int,
                              wintypes.DWORD, ctypes.c_size_t]
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
kernel32.GetCurrentThreadId.restype = wintypes.DWORD

WM_SETTEXT, EM_SETSEL, WM_QUIT = 0x000C, 0x00B1, 0x0012
MOUSEEVENTF_MOVE, MOUSEEVENTF_LEFTDOWN, MOUSEEVENTF_LEFTUP = 0x0001, 0x0002, 0x0004
GW_CHILD, GW_HWNDNEXT = 5, 2
TITLE = "Reader - e2e.txt"
SENTENCE = "The ephemeral glow faded quickly today."

failures: list[str] = []


def check(name: str, cond: bool, extra: str = "") -> None:
    print(f"{'PASS' if cond else 'FAIL'}  {name}{(' :: ' + extra) if extra else ''}")
    if not cond:
        failures.append(name)


def api(base: str, path: str, payload=None, method: str = "GET"):
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(
        base + path, data=data, method=method,
        headers={"Content-Type": "application/json"} if data else {})
    with urllib.request.urlopen(req, timeout=10) as resp:
        return json.loads(resp.read().decode("utf-8"))


def find_edit(hwnd: int) -> int:
    child = int(user32.GetWindow(hwnd, GW_CHILD) or 0)
    while child:
        if capture.get_class_name(child).lower() == "edit":
            return child
        child = int(user32.GetWindow(child, GW_HWNDNEXT) or 0)
    return 0


def clipboard_text() -> str | None:
    import win32clipboard

    try:
        win32clipboard.OpenClipboard()
    except Exception:
        return None
    try:
        if win32clipboard.IsClipboardFormatAvailable(win32clipboard.CF_UNICODETEXT):
            return str(win32clipboard.GetClipboardData(win32clipboard.CF_UNICODETEXT))
        return None
    finally:
        win32clipboard.CloseClipboard()


def set_clipboard(text: str) -> None:
    import win32clipboard

    win32clipboard.OpenClipboard()
    try:
        win32clipboard.EmptyClipboard()
        win32clipboard.SetClipboardData(win32clipboard.CF_UNICODETEXT, text)
    finally:
        win32clipboard.CloseClipboard()


def focus(hwnd: int) -> bool:
    fg = user32.GetForegroundWindow()
    cur = kernel32.GetCurrentThreadId()
    target = user32.GetWindowThreadProcessId(fg, None) if fg else cur
    attached = bool(user32.AttachThreadInput(target, cur, True))
    user32.BringWindowToTop(hwnd)
    user32.SetForegroundWindow(hwnd)
    if attached:
        user32.AttachThreadInput(target, cur, False)
    time.sleep(0.3)
    return int(user32.GetForegroundWindow()) == hwnd


def double_click(x: int, y: int) -> None:
    user32.SetCursorPos(x, y)
    time.sleep(0.05)
    for _ in range(2):
        user32.mouse_event(MOUSEEVENTF_LEFTDOWN, 0, 0, 0, 0)
        time.sleep(0.03)
        user32.mouse_event(MOUSEEVENTF_LEFTUP, 0, 0, 0, 0)
        time.sleep(0.08)


def click_until_seen(hwnd: int, watcher, x: int, y: int, seen: list, *also_clear,
                     attempts: int = 4) -> int:
    """Click until the low-level hook reports the button-downs.

    Synthetic clicks only land on the foreground window, and Windows happily
    lets something else keep it (a browser restoring a tab, a tray app drawing
    a panel, an IDE saving). Then the click silently goes nowhere and the hook
    sees nothing at all -- so re-focus and retry rather than failing the run.
    Returns how many attempts it took.
    """
    for attempt in range(1, attempts + 1):
        focus(hwnd)
        watcher._suppress = 0.0
        seen.clear()
        for extra in also_clear:
            extra.clear()
        double_click(x, y)
        for _ in range(25):
            if len(seen) >= 2:
                return attempt
            time.sleep(0.1)
        print(f"   (attempt {attempt}: the desktop swallowed the click, retrying)")
    return attempts


def main() -> int:
    srv = server.ApiServer("127.0.0.1", 8802)
    base = f"http://127.0.0.1:{srv.start()}"

    proc = subprocess.Popen([sys.executable, os.path.join(HERE, "probe_host.py"), TITLE],
                            stdout=subprocess.PIPE, text=True)
    hwnd = int(proc.stdout.readline().strip() or 0)
    check("probe process window created", hwnd != 0, str(hwnd))
    if not hwnd:
        proc.kill()
        return 1

    edit = find_edit(hwnd)
    check("found edit control", edit != 0, str(edit))
    pid = wintypes.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    check("probe runs in another process", pid.value != os.getpid(),
          f"{pid.value} vs {os.getpid()}")

    fired: list[tuple] = []
    failed: list[object] = []

    def on_double_click(x, y, h, title, cls):
        fired.append((x, y, title, cls))
        result = capture.capture_word(x, y)
        if not result:
            failed.append("capture_word returned None")
            return
        api(base, "/add", {
            "word": result["word"],
            "sentence": result["sentence"] or None,
            "source": title.split(" - ")[0],
            "method": f"dblclick:{result['method']}",
        }, "POST")

    seen: list[tuple[int, int, int]] = []
    orig_handle = capture.DoubleClickWatcher._mouse_event

    def counting_hook(self, code, wparam, lparam):
        if wparam == capture.WM_LBUTTONDOWN:
            info = capture.MSLLHOOKSTRUCT()
            ctypes.memmove(ctypes.byref(info), ctypes.c_void_p(lparam),
                           ctypes.sizeof(info))
            seen.append((info.pt.x, info.pt.y, info.time))
        return orig_handle(self, code, wparam, lparam)

    capture.DoubleClickWatcher._mouse_event = counting_hook  # type: ignore[method-assign]

    watcher = capture.DoubleClickWatcher(on_double_click)
    watcher.start()
    time.sleep(0.4)
    check("hook installed", watcher._hook_id is not None)

    focused = focus(hwnd)
    check("probe window focused", focused, str(capture.get_foreground_window()))

    if not focused:
        print("\nSKIP  double-click test (desktop not focusable)")
    else:
        # point at the middle of "ephemeral" in the edit control
        pt = capture.POINT(90, 14)
        user32.ClientToScreen(edit, ctypes.byref(pt))

        set_clipboard("UNRELATED-TEXT")
        # DPI scaling: SetCursorPos and the low-level hook use different
        # coordinate spaces, so read the physical position back after moving.
        focus(hwnd)
        user32.SetCursorPos(pt.x, pt.y)
        time.sleep(0.15)
        cursor = capture.POINT()
        user32.GetCursorPos(ctypes.byref(cursor))
        check("cursor parked over the word",
              abs(cursor.x - pt.x) <= 2 and abs(cursor.y - pt.y) <= 2,
              f"cursor={cursor.x},{cursor.y} target={pt.x},{pt.y}")
        tries = click_until_seen(hwnd, watcher, cursor.x, cursor.y, seen, fired, failed)
        check("double click reached the desktop", tries < 4, f"{tries} attempt(s)")
        time.sleep(1.5)
        check("hook saw two button-down events", len(seen) == 2, str(seen))
        check("capture callback fired", bool(fired), str(fired[:1]))
        check("capture_word produced a result", not failed, str(failed[:1]))
        check("DPI awareness enabled", capture.enable_dpi_awareness() != "none")
        check("click coordinates match the cursor",
              bool(fired) and abs(fired[0][0] - cursor.x) <= 2
              and abs(fired[0][1] - cursor.y) <= 2,
              f"hook={fired[0][:2] if fired else None} cursor={cursor.x},{cursor.y}")

        words = api(base, "/words?limit=50")["words"]
        hit = [w for w in words if w["word"].lower() == "ephemeral"]
        check("double click stored the word", bool(hit), str([w["word"] for w in words]))
        check("clipboard left untouched", clipboard_text() == "UNRELATED-TEXT",
              repr(clipboard_text()))

        click_until_seen(hwnd, watcher, pt.x, pt.y, seen, fired, failed)
        time.sleep(1.5)
        words2 = api(base, "/words?limit=50")["words"]
        hit2 = [w for w in words2 if w["word"].lower() == "ephemeral"]
        check("repeat capture merges into one row", len(hit2) == 1, str(len(hit2)))
        check("hit counter incremented", bool(hit2) and hit2[0]["hits"] == 2,
              str(hit2[0]["hits"] if hit2 else None))

        caps = api(base, "/captures?limit=10")["captures"]
        check("capture log records method",
              any(c["word"] == "ephemeral" and "dblclick" in (c["method"] or "") for c in caps),
              str([c["method"] for c in caps[:3]]))
        check("capture log records source app",
              any(c["source"] == "Reader" for c in caps), str([c["source"] for c in caps[:3]]))

        stats = api(base, "/stats")
        check("stats reflect the capture", stats["total"] >= 1, json.dumps(stats))

    watcher.stop()
    srv.stop()
    proc.terminate()
    try:
        proc.wait(3)
    except subprocess.TimeoutExpired:
        proc.kill()
    verify()
    print()
    print("ALL PASS" if not failures else "FAILURES: " + ", ".join(failures))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
"""Regression test for the crash the user hit:

    RuntimeError: main thread is not in main loop

Tk is not thread-safe. The mouse-hook thread used to call panel.after()
directly, so every single capture raised and nothing was ever shown to the user.

This launches the REAL app (python -m wordgrab) as a subprocess, then fires
real double clicks at a real editor window in another process and asserts the
capture lands in the app's own database with no errors in its log.
"""

from __future__ import annotations

import os
import subprocess
import sys
import time
from ctypes import wintypes

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)

from sandbox import activate, verify  # noqa: E402

# this test spawns the REAL app, so the temp dir is passed through the
# environment to the child process rather than used in-process
APP_DATA = activate("wordgrab_regress_data_")
failures: list[str] = []


def check(name: str, cond: bool, extra: str = "") -> None:
    print(f"{'PASS' if cond else 'FAIL'}  {name}{(' :: ' + extra) if extra else ''}")
    if not cond:
        failures.append(name)


def main() -> int:
    import ctypes

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    user32.SetCursorPos.argtypes = [ctypes.c_int, ctypes.c_int]
    user32.SetForegroundWindow.argtypes = [wintypes.HWND]
    user32.BringWindowToTop.argtypes = [wintypes.HWND]
    user32.GetForegroundWindow.restype = wintypes.HWND
    user32.GetWindow.argtypes = [wintypes.HWND, ctypes.c_uint]
    user32.GetWindow.restype = wintypes.HWND
    user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    user32.GetWindowThreadProcessId.restype = wintypes.DWORD
    user32.AttachThreadInput.argtypes = [wintypes.DWORD, wintypes.DWORD, ctypes.c_int]
    user32.mouse_event.argtypes = [wintypes.DWORD, ctypes.c_int, ctypes.c_int,
                                  wintypes.DWORD, ctypes.c_size_t]
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.GetCurrentThreadId.restype = wintypes.DWORD

    sys.path.insert(0, ROOT)
    from wordgrab import capture

    python_exe = os.path.join(ROOT, ".venv", "Scripts", "python.exe")
    if not os.path.exists(python_exe):
        python_exe = sys.executable

    log_path = os.path.join(str(APP_DATA), "wordgrab.log")
    env = dict(os.environ, WORDGRAB_DATA=str(APP_DATA), PYTHONPATH=ROOT)

    # ---------------------------------------------------------------- launch
    app = subprocess.Popen([python_exe, "-m", "wordgrab", "--minimized"],
                           cwd=ROOT, env=env,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    import httpx

    ready = False
    for _ in range(40):
        time.sleep(0.5)
        try:
            if httpx.get("http://127.0.0.1:8731/health", timeout=2).status_code == 200:
                ready = True
                break
        except Exception:
            continue
    check("real app started and serves its API", ready)
    if not ready:
        app.terminate()
        verify()
        return 1

    # ------------------------------------------------------------- target app
    reader = subprocess.Popen([python_exe, os.path.join(HERE, "probe_host.py"),
                               "Reader - regression.txt"],
                              stdout=subprocess.PIPE, text=True)
    hwnd = int(reader.stdout.readline().strip() or 0)
    check("editor window created in another process", hwnd != 0, str(hwnd))

    def find_edit(parent: int) -> int:
        child = int(user32.GetWindow(parent, 5) or 0)
        while child:
            if capture.get_class_name(child).lower() == "edit":
                return child
            child = int(user32.GetWindow(child, 2) or 0)
        return 0

    def focus(h: int) -> bool:
        fg = user32.GetForegroundWindow()
        cur = kernel32.GetCurrentThreadId()
        target = user32.GetWindowThreadProcessId(fg, None) if fg else cur
        attached = bool(user32.AttachThreadInput(target, cur, True))
        user32.BringWindowToTop(h)
        user32.SetForegroundWindow(h)
        if attached:
            user32.AttachThreadInput(target, cur, False)
        time.sleep(0.35)
        return int(user32.GetForegroundWindow()) == h

    def dblclick(x: int, y: int) -> None:
        user32.SetCursorPos(x, y)
        time.sleep(0.05)
        for _ in range(2):
            user32.mouse_event(0x0002, 0, 0, 0, 0)
            time.sleep(0.03)
            user32.mouse_event(0x0004, 0, 0, 0, 0)
            time.sleep(0.08)

    edit = find_edit(hwnd)
    check("found the text control", edit != 0, str(edit))

    point = capture.POINT(90, 14)
    user32.ClientToScreen(edit, ctypes.byref(point))
    check("target window focused", focus(hwnd), str(capture.get_foreground_window()))

    # ------------------------------------------------------- the real capture
    for attempt in range(2):
        focus(hwnd)
        dblclick(point.x, point.y)
        time.sleep(2.5)
        words = httpx.get("http://127.0.0.1:8731/words?limit=20", timeout=10).json()["words"]
        if any(w["word"].lower() == "ephemeral" for w in words):
            break
        print(f"   (attempt {attempt + 1}: no capture yet, retrying)")

    words = httpx.get("http://127.0.0.1:8731/words?limit=20", timeout=10).json()["words"]
    hit = [w for w in words if w["word"].lower() == "ephemeral"]
    check("capture reached the running app's database", bool(hit),
          str([w["word"] for w in words]))

    stats = httpx.get("http://127.0.0.1:8731/stats", timeout=10).json()
    check("stats updated", stats["total"] >= 1, str(stats))

    # ------------------------------------------------------------- no crashes
    time.sleep(1.0)
    app.terminate()
    try:
        app.wait(6)
    except subprocess.TimeoutExpired:
        app.kill()
    reader.terminate()

    log = ""
    if os.path.exists(log_path):
        log = open(log_path, encoding="utf-8", errors="replace").read()
    check("log has no 'main thread is not in main loop'",
          "main thread is not in main loop" not in log)
    check("log has no ERROR entries", "ERROR" not in log,
          "; ".join(line for line in log.splitlines() if "ERROR" in line)[:300])
    if log.strip():
        print("   log tail:", log.strip().splitlines()[-1][:160])

    verify()
    print()
    print("ALL PASS" if not failures else "FAILURES: " + ", ".join(failures))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
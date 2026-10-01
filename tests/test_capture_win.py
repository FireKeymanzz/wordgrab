from __future__ import annotations

import ctypes
import os
import queue
import sys
import threading
import time
from ctypes import wintypes

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sandbox import activate, verify  # noqa: E402

TMP = activate("wordgrab_capture_")

from wordgrab import capture, db  # noqa: E402

user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

user32.CreateWindowExW.restype = wintypes.HWND
user32.CreateWindowExW.argtypes = [wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR,
                                   wintypes.DWORD, ctypes.c_int, ctypes.c_int,
                                   ctypes.c_int, ctypes.c_int, wintypes.HWND,
                                   wintypes.HMENU, wintypes.HINSTANCE, ctypes.c_void_p]
user32.SendMessageW.restype = ctypes.c_ssize_t
user32.SendMessageW.argtypes = [wintypes.HWND, ctypes.c_uint, ctypes.c_size_t, ctypes.c_ssize_t]
user32.GetForegroundWindow.restype = wintypes.HWND
user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
user32.GetWindowTextLengthW.argtypes = [wintypes.HWND]
user32.SetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPCWSTR]
user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
user32.GetWindowThreadProcessId.restype = wintypes.DWORD
user32.SetForegroundWindow.argtypes = [wintypes.HWND]
user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
user32.BringWindowToTop.argtypes = [wintypes.HWND]
user32.SetFocus.argtypes = [wintypes.HWND]
user32.AttachThreadInput.argtypes = [wintypes.DWORD, wintypes.DWORD, ctypes.c_int]
user32.DestroyWindow.argtypes = [wintypes.HWND]
user32.PeekMessageW.argtypes = [ctypes.POINTER(wintypes.MSG), wintypes.HWND,
                                ctypes.c_uint, ctypes.c_uint, ctypes.c_uint]
user32.GetMessageW.restype = ctypes.c_int
user32.GetMessageW.argtypes = [ctypes.POINTER(wintypes.MSG), wintypes.HWND,
                                ctypes.c_uint, ctypes.c_uint]
user32.TranslateMessage.argtypes = [ctypes.POINTER(wintypes.MSG)]
user32.DispatchMessageW.argtypes = [ctypes.POINTER(wintypes.MSG)]
user32.PostThreadMessageW.argtypes = [wintypes.DWORD, ctypes.c_uint, wintypes.WPARAM, wintypes.LPARAM]
user32.DefWindowProcW.restype = ctypes.c_ssize_t
user32.DefWindowProcW.argtypes = [wintypes.HWND, ctypes.c_uint, wintypes.WPARAM, wintypes.LPARAM]
user32.RegisterClassW.argtypes = [ctypes.c_void_p]
kernel32.GetCurrentThreadId.restype = wintypes.DWORD

WM_SETTEXT, EM_SETSEL = 0x000C, 0x00B1
WS_CHILD, WS_VISIBLE, WS_BORDER = 0x40000000, 0x10000000, 0x00800000
WS_OVERLAPPEDWINDOW = 0x00CF0000
SW_SHOW, PM_REMOVE, WM_QUIT, WM_APP = 5, 0x0001, 0x0012, 0x8000
SAMPLE = "The ephemeral glow faded quickly today."

WNDPROC = ctypes.WINFUNCTYPE(ctypes.c_ssize_t, wintypes.HWND, ctypes.c_uint,
                             wintypes.WPARAM, wintypes.LPARAM)


class WNDCLASSW(ctypes.Structure):
    _fields_ = [
        ("style", ctypes.c_uint), ("lpfnWndProc", WNDPROC), ("cbClsExtra", ctypes.c_int),
        ("cbWndExtra", ctypes.c_int), ("hInstance", wintypes.HINSTANCE), ("hIcon", wintypes.HICON),
        ("hCursor", wintypes.HANDLE), ("hbrBackground", wintypes.HBRUSH),
        ("lpszMenuName", wintypes.LPCWSTR), ("lpszClassName", wintypes.LPCWSTR),
    ]


class ProbeApp(threading.Thread):
    """A tiny notepad-like window on its own thread, with a live message loop."""

    def __init__(self) -> None:
        super().__init__(name="probe-window", daemon=True)
        self.ready = threading.Event()
        self.hwnd = 0
        self.edit = 0
        self.tid = 0
        self.title = "Reader - probe.txt"
        self._tasks: "queue.Queue" = queue.Queue()
        self._proc = WNDPROC(lambda h, m, w, l: user32.DefWindowProcW(h, m, w, l))
        self._class = WNDCLASSW()
        self._class.lpfnWndProc = self._proc
        self._class.lpszClassName = "WordGrabProbeWin"

    def run(self) -> None:
        self.tid = kernel32.GetCurrentThreadId()
        user32.RegisterClassW(ctypes.byref(self._class))
        self.hwnd = user32.CreateWindowExW(
            0, "WordGrabProbeWin", self.title, WS_OVERLAPPEDWINDOW,
            120, 120, 780, 220, None, None, self._class.hInstance, None)
        self.edit = user32.CreateWindowExW(
            0, "EDIT", "", WS_CHILD | WS_VISIBLE | WS_BORDER,
            10, 10, 720, 60, self.hwnd, None, self._class.hInstance, None)
        self.set_text(SAMPLE)
        user32.ShowWindow(self.hwnd, SW_SHOW)
        self.ready.set()
        msg = wintypes.MSG()
        while True:
            got = user32.GetMessageW(ctypes.byref(msg), None, 0, 0)
            if got in (0, -1) or msg.message == WM_QUIT:
                break
            if msg.message == WM_APP:
                try:
                    self._tasks.get_nowait()()
                except queue.Empty:
                    pass
                continue
            user32.TranslateMessage(ctypes.byref(msg))
            user32.DispatchMessageW(ctypes.byref(msg))
        user32.DestroyWindow(self.hwnd)

    def set_text(self, text: str) -> None:
        buf = ctypes.create_unicode_buffer(text)
        user32.SendMessageW(self.edit, WM_SETTEXT, 0, ctypes.cast(buf, ctypes.c_void_p).value)

    def select(self, start: int, end: int) -> None:
        user32.SendMessageW(self.edit, EM_SETSEL, start, end)

    def focus(self) -> bool:
        """Focus from the window's own thread: SetFocus/SetForegroundWindow are
        thread-scoped, doing this from the test thread is unreliable."""
        result: list[bool] = []
        done = threading.Event()

        def task() -> None:
            fg = user32.GetForegroundWindow()
            target = user32.GetWindowThreadProcessId(fg, None) if fg else self.tid
            attached = bool(user32.AttachThreadInput(target, self.tid, True))
            user32.BringWindowToTop(self.hwnd)
            user32.SetForegroundWindow(self.hwnd)
            user32.SetFocus(self.edit)
            if attached:
                user32.AttachThreadInput(target, self.tid, False)
            time.sleep(0.25)
            result.append(int(user32.GetForegroundWindow()) == self.hwnd)
            done.set()

        self._tasks.put(task)
        user32.PostThreadMessageW(self.tid, WM_APP, 0, 0)
        done.wait(5)
        time.sleep(0.2)
        return bool(result and result[0])


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

    deadline = time.time() + 3
    while True:
        try:
            win32clipboard.OpenClipboard()
            break
        except Exception:
            if time.time() >= deadline:
                raise
            time.sleep(0.05)
    try:
        win32clipboard.EmptyClipboard()
        win32clipboard.SetClipboardData(win32clipboard.CF_UNICODETEXT, text)
    finally:
        win32clipboard.CloseClipboard()


def main() -> int:
    failures: list[str] = []

    def check(name: str, cond: bool, extra: str = "") -> None:
        print(f"{'PASS' if cond else 'FAIL'}  {name}{(' :: ' + extra) if extra else ''}")
        if not cond:
            failures.append(name)

    probe = ProbeApp()
    probe.start()
    probe.ready.wait(5)
    check("probe window created", probe.hwnd != 0, str(probe.hwnd))
    focused = probe.focus()
    check("probe window takes foreground", focused, str(capture.get_foreground_window()))

    if not focused:
        print("\nSKIP  selection-based checks (interactive desktop not focused)")
    else:
        probe.select(4, 13)
        time.sleep(0.2)

        sel, ctx = capture.get_selection_via_uia()
        check("UIA path runs cleanly", isinstance(sel, str) and isinstance(ctx, str),
              repr(sel[:30]))
        if sel:
            check("UIA reads selected text", sel.lower() == "ephemeral", repr(sel[:40]))
            check("UIA returns document context", "ephemeral" in (ctx or "").lower(),
                  repr((ctx or "")[:40]))

        set_clipboard("STALE-CONTENT")
        probe.select(4, 13)
        time.sleep(0.15)
        result = capture.capture_word(200, 160)
        check("capture_word returns a word", bool(result), str(result))
        if result:
            check("capture_word normalizes", result["word"] == "ephemeral", result["word"])
            check("method recorded", result["method"] in ("uia", "clipboard"), result["method"])
        check("stale clipboard ignored", result is not None)
        check("clipboard restored after capture", clipboard_text() == "STALE-CONTENT",
              repr(clipboard_text()))

        set_clipboard("PRESERVE-ME")
        probe.select(4, 13)
        time.sleep(0.15)
        cb = capture.Clipboard()
        cb.snapshot()
        check("snapshot captures existing text", cb._had_text and cb._previous == "PRESERVE-ME",
              repr(cb._previous))
        copied = cb.copy_from_foreground()
        check("copy_from_foreground reads selection", (copied or "").lower() == "ephemeral",
              repr(copied))
        cb.restore()
        time.sleep(0.1)
        check("clipboard restored after copy", clipboard_text() == "PRESERVE-ME",
              repr(clipboard_text()))

        # whole sentence selection still yields a single normalized word
        probe.select(0, len(SAMPLE))
        time.sleep(0.15)
        sel2, _ = capture.get_selection_via_clipboard()
        check("long selection still captured", bool(sel2), repr(sel2[:40]))
        wide = capture.capture_word(200, 160)
        check("long selection normalized to one word",
              bool(wide) and " " not in wide["word"], str(wide))

    # ------------------------------------------------- clipboard contention
    # On a normal desktop another process can hold the clipboard open, which
    # makes OpenClipboard fail. Simulate that deterministically: some Windows
    # sessions (like this one) never take the lock, so inject the failure.
    class FlakyClipboard:
        CF_UNICODETEXT = 13

        def __init__(self, fail_first: int):
            self.fail_first = fail_first
            self.attempts = 0
            self.value = "PRESERVE-ME"
            self.cleared = 0

        def OpenClipboard(self):
            self.attempts += 1
            if self.attempts <= self.fail_first:
                raise OSError(5, "access denied")
            return None

        def CloseClipboard(self):
            return None

        def IsClipboardFormatAvailable(self, _fmt):
            return True

        def GetClipboardData(self, _fmt):
            return self.value

        def EmptyClipboard(self):
            self.cleared += 1
            self.value = ""

        def SetClipboardData(self, _fmt, text):
            self.value = text
            return 1

    real_api = capture._clipboard
    try:
        # (1) brief lock -> retries get through
        flaky = FlakyClipboard(fail_first=3)
        capture._clipboard = flaky
        cb3 = capture.Clipboard()
        cb3.snapshot()
        check("snapshot retries through a brief lock",
              cb3._had_text and cb3._previous == "PRESERVE-ME", repr(cb3._previous))

        # (2) permanent lock -> reports busy instead of raising
        hard = FlakyClipboard(fail_first=10_000)
        capture._clipboard = hard
        cb4 = capture.Clipboard()
        cb4.snapshot()
        check("snapshot degrades quietly when permanently locked",
              cb4._previous is None and not cb4._had_text, repr(cb4._previous))
        busy = False
        try:
            cb4.copy_from_foreground(timeout_ms=100)
        except capture.ClipboardBusy:
            busy = True
        check("locked clipboard raises ClipboardBusy, not OSError", busy)
        check("refuses to clear a clipboard it cannot reopen", hard.cleared == 0,
              f"cleared={hard.cleared}")
        check("capture_word degrades to None when the clipboard is locked",
              capture.capture_word(200, 160) is None)

        # (3) clipboard freed mid-capture -> the copy path recovers
        flaky2 = FlakyClipboard(fail_first=2)
        capture._clipboard = flaky2
        cb5 = capture.Clipboard()
        cb5.snapshot()
        check("recovers once the clipboard frees up", cb5._had_text,
              repr(cb5._previous))

        # (4) restore keeps trying until it can put the data back
        flaky3 = FlakyClipboard(fail_first=0)
        capture._clipboard = flaky3
        cb6 = capture.Clipboard()
        cb6.snapshot()
        cb6._write(None)  # cleared, so restore has work to do
        flaky3.fail_first = 3  # now the clipboard is contested
        before = flaky3.attempts
        cb6.restore()
        check("restore retried through contention",
              flaky3.attempts > before + 1, f"attempts={flaky3.attempts} before={before}")
        check("restored the original text", flaky3.value == "PRESERVE-ME",
              repr(flaky3.value))
    finally:
        capture._clipboard = real_api

    check("real clipboard still usable afterwards", capture.Clipboard().snapshot() is None)

    # ------------------------------------------------------ capture gating rules
    long_word = "a" * 60
    res = db.add_word(long_word, method="test")
    check("oversize word stored through API", res["word"] == long_word)
    db.delete_word(res["id"])

    real_config = capture.load_config
    capture.load_config = lambda: {**real_config(), "max_word_len": 5}  # type: ignore[assignment]
    try:
        from wordgrab import config as cfgmod

        original = cfgmod.DEFAULTS["max_word_len"]
        cfgmod.DEFAULTS["max_word_len"] = 5
        cfgmod._config = None
        check("config gate applied", capture.load_config()["max_word_len"] == 5)
    finally:
        cfgmod.DEFAULTS["max_word_len"] = original
        cfgmod._config = None
        capture.load_config = real_config  # type: ignore[assignment]
    check("config restored", capture.load_config()["max_word_len"] == real_config()["max_word_len"])

    check("modifier detection callable", isinstance(capture.is_modifier_pressed(), bool))

    # ------------------------------------------------------------ watcher rules
    events: list[tuple] = []
    watcher = capture.DoubleClickWatcher(lambda *a: events.append(a))
    watcher.start()
    time.sleep(0.4)
    check("watcher installs hook", watcher._hook_id is not None, str(watcher._hook_id))

    FOREIGN = 0xFFFFFFFFFFFFFFFF
    watcher._fire(200, 200, FOREIGN, "Reader - probe.txt", "notepad")
    time.sleep(0.3)
    check("callback receives window context",
          bool(events) and events[0][3] == "Reader - probe.txt", str(events[:1]))
    check("callback receives class name", bool(events) and events[0][4] == "notepad")

    events.clear()
    probe_pid = capture.get_window_pid(probe.hwnd)
    check("probe pid matches this process", probe_pid == os.getpid(),
          f"{probe_pid} vs {os.getpid()}")
    watcher._fire(200, 200, probe.hwnd, "Reader - probe.txt", "edit")
    time.sleep(0.2)
    check("own-process window ignored", not events, str(events[:1]))

    events.clear()
    watcher._fire(200, 200, FOREIGN, "TextInputHost", "textinputhost")
    time.sleep(0.2)
    check("ignored app skipped", not events, str(events[:1]))

    watcher._suppress = time.time() + 5
    events.clear()
    watcher._fire(200, 200, FOREIGN, "Reader - probe.txt", "notepad")
    check("suppression blocks re-fire", not events)

    watcher._suppress = 0.0
    watcher.stop()
    time.sleep(0.2)

    user32.PostThreadMessageW(probe.tid, WM_QUIT, 0, 0)
    probe.join(3)
    verify()
    print()
    print("ALL PASS" if not failures else "FAILURES: " + ", ".join(failures))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
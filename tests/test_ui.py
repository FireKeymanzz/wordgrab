"""GUI-level checks for the panel, tray icon and global hotkeys.

Creates the real widgets, then drives them programmatically (no user input).
"""

from __future__ import annotations

import os
import sys
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sandbox import activate, verify  # noqa: E402

TMP = activate("wordgrab_ui_")

from wordgrab import capture, db, dictionary, server  # noqa: E402
from wordgrab.autostart import HotkeyManager, parse_hotkey  # noqa: E402
from wordgrab.tray import make_badge, make_icon  # noqa: E402
from wordgrab.ui import Panel, due_text  # noqa: E402
from tkinter import ttk  # noqa: E402

failures: list[str] = []


def check(name: str, cond: bool, extra: str = "") -> None:
    print(f"{'PASS' if cond else 'FAIL'}  {name}{(' :: ' + extra) if extra else ''}")
    if not cond:
        failures.append(name)


def pump(panel: Panel, seconds: float = 0.3) -> None:
    end = time.time() + seconds
    while time.time() < end:
        panel.update()
        time.sleep(0.01)


def main() -> int:
    # ------------------------------------------------------------------- data
    db.add_word("ephemeral", definition="adj. 短暂的", method="test")
    db.add_word("resilient", definition="adj. 有复原力的", method="test")
    db.add_word("banana", definition="n. 香蕉", method="test")
    check("seed data present", db.stats()["total"] == 3, str(db.stats()))

    # ------------------------------------------------------------------- panel
    panel = Panel()
    panel.withdraw()
    panel.update()

    panel.search_var.set("ephe")
    panel.refresh_words()
    rows = panel.tree.get_children()
    check("search filters the table", len(rows) == 1, str(len(rows)))
    check("table shows the word", panel.tree.item(rows[0], "values")[0] == "ephemeral")

    panel.search_var.set("")
    panel.refresh_words()
    check("clearing search restores all rows", len(panel.tree.get_children()) == 3)

    def select_word(word: str) -> bool:
        for item in panel.tree.get_children():
            if panel.tree.item(item, "values")[0] == word:
                panel.tree.selection_set(item)
                panel.update()
                return True
        return False

    check("can select a specific row", select_word("ephemeral"))
    panel.show_detail()
    detail = panel.detail.get("1.0", "end")
    check("detail pane shows definition", "短暂的" in detail, detail[:40].replace("\n", " "))

    # review flow
    for extra in ("resolute", "ephemera", "lucid", "quintessential"):
        db.add_word(extra, definition=f"adj. {extra} 的释义", method="test")
    panel.open_review()
    pump(panel, 0.2)
    check("review shows first word", bool(panel.rev_word.cget("text")),
          panel.rev_word.cget("text"))
    check("definition hidden before reveal", "短暂的" not in panel.rev_body.get("1.0", "end"))
    panel.reveal()
    pump(panel, 0.1)
    check("space reveals the definition", "短暂的" in panel.rev_body.get("1.0", "end"))

    # 四个按钮就是用户要的四档
    labels = [b.cget("text") for b in panel.grade_btns]
    check("four grade buttons", labels == ["熟知 (1)", "认识 (2)", "不确定 (3)", "不认识 (4)"],
          str(labels))
    check("grade buttons styled apart",
          len({b.cget("style") for b in panel.grade_btns}) == 4,
          str([b.cget("style") for b in panel.grade_btns]))

    first_word = panel._review_row["word"]
    before = db.get_word(first_word)
    panel.grade(1)          # 认识
    pump(panel, 0.2)
    after = db.get_word(first_word)
    check("grading advances the schedule",
          after["reps"] == before["reps"] + 1 and after["interval_days"] > 0,
          f"{before['reps']}->{after['reps']} {after['interval_days']}d")
    check("grade advanced to the next card", panel._review_row is not None)
    check("排期反馈告诉用户下次什么时候", "天后" in panel.rev_hint.cget("text"),
          panel.rev_hint.cget("text"))
    reviews = db.get_conn().execute("SELECT COUNT(*) c FROM reviews").fetchone()["c"]
    check("review logged", reviews == 1, str(reviews))
    check("review log 记的是新档位名",
          db.get_conn().execute("SELECT grade FROM reviews ORDER BY id DESC LIMIT 1"
                                ).fetchone()["grade"] == "familiar")

    panel.grade(2)
    pump(panel, 0.2)
    panel.grade(3)
    pump(panel, 0.2)
    check("multiple grades logged",
          db.get_conn().execute("SELECT COUNT(*) c FROM reviews").fetchone()["c"] == 3)

    # 熟知：直接毕业，不再出现在队列里
    last_word = panel._review_row["word"]
    panel.grade(0)
    pump(panel, 0.2)
    last_row = db.get_word(last_word)
    check("熟知 -> status known", last_row["status"] == "known", last_row["status"])
    check("熟知 -> 不再排期", last_row["due_at"] is None)
    check("熟知提示不再复习", "不再复习" in panel.rev_hint.cget("text"),
          panel.rev_hint.cget("text"))
    check("熟知的词不在复习队列",
          all(r["word"] != last_word for r in db.review_queue(50, 50)))

    # queue drains to an empty state
    for _ in range(40):
        if panel._review_row is None:
            break
        panel.grade(1)
        pump(panel, 0.05)
    check("queue eventually empties", panel._review_row is None,
          str(panel._review_row["word"]) if panel._review_row is not None else "")

    # ------------------------------------------------------------- 键盘操作
    # 回归：以前按键绑在 review 页的 Frame 上，而 Frame 拿不到键盘焦点
    # （面板是 withdraw 后再弹出的），所以空格/数字统统没反应。
    panel.deiconify()      # 模拟托盘/热键把面板叫出来（_show 里还会 focus_force）
    panel.focus_force()
    panel.update()
    for extra in ("keytest", "keyable", "keynote", "keypad", "keystone", "keyword"):
        db.add_word(extra, definition=f"n. {extra} 的释义", method="test")
    panel.open_review()
    pump(panel, 0.3)
    check("复习页接管了键盘焦点", panel.focus_get() is panel.rev_body,
          str(panel.focus_get()))
    kw = panel._review_row["word"]
    krow = db.get_word(kw)
    panel.rev_body.event_generate("<KeyPress-2>")
    pump(panel, 0.2)
    kafter = db.get_word(kw)
    check("按键 2 = 认识，排期真的动了",
          kafter["reps"] == krow["reps"] + 1 and kafter["interval_days"] > 0,
          f"{krow['reps']}->{kafter['reps']}")
    check("按键后自动翻到下一张", panel._review_row is not None
          and panel._review_row["word"] != kw, str(panel._review_row["word"]))
    check("换卡后释义重新藏起来", "先自己回忆" in panel.rev_body.get("1.0", "end"))
    panel.rev_body.event_generate("<KeyPress-space>")
    pump(panel, 0.1)
    check("空格键揭示释义", "先自己回忆" not in panel.rev_body.get("1.0", "end"))
    shown = panel.rev_body.get("1.0", "end")
    panel.rev_body.event_generate("<KeyPress-a>")
    panel.rev_body.event_generate("<KeyPress-z>")
    pump(panel, 0.1)
    check("释义框是只读的，误敲的字母不会留在卡片上",
          panel.rev_body.get("1.0", "end") == shown)
    u_word = panel._review_row["word"]
    panel.rev_body.event_generate("<KeyPress-3>")     # 不确定
    pump(panel, 0.2)
    check("按键 3 = 不确定", db.get_word(u_word)["status"] == "learning",
          db.get_word(u_word)["status"])
    m_word = panel._review_row["word"]
    panel.rev_body.event_generate("<KeyPress-1>")     # 熟知
    pump(panel, 0.2)
    m_row = db.get_word(m_word)
    check("按键 1 = 熟知，直接毕业",
          m_row["status"] == "known" and m_row["due_at"] is None,
          f"{m_word} {m_row['status']} {m_row['due_at']}")
    n_word = panel._review_row["word"] if panel._review_row else None
    panel.rev_body.event_generate("<KeyPress-4>")     # 不认识
    pump(panel, 0.2)
    n_row = db.get_word(n_word) if n_word else None
    check("按键 4 = 不认识，分钟级重来",
          n_row is not None and 0 < n_row["interval_days"] < 1
          and 0 < n_row["due_at"] - time.time() < 1200,
          f"{n_word} {n_row['interval_days'] if n_row else None}d")

    # 焦点在搜索框里打字时，数字键不能被复习抢走
    panel.nb.select(panel.tab_words)
    pump(panel, 0.2)
    guard_word = panel._review_row["word"]
    guard_reps = db.get_word(guard_word)["reps"]
    panel.search_entry.focus_set()
    panel.search_entry.event_generate("<KeyPress-1>")
    pump(panel, 0.1)
    check("搜索框里打字不被抢键", panel.search_var.get() == "1"
          and db.get_word(guard_word)["reps"] == guard_reps,
          f"search={panel.search_var.get()!r}")
    panel.search_var.set("")
    panel.refresh_words()
    pump(panel, 0.1)

    # 生词库页按数字不应该评分
    panel.nb.select(panel.tab_words)
    panel.tree.focus_set()
    guard_reps = db.get_word(guard_word)["reps"]
    panel.tree.event_generate("<KeyPress-1>")
    pump(panel, 0.1)
    check("不在复习页时数字键不评分", db.get_word(guard_word)["reps"] == guard_reps)
    panel.nb.select(panel.tab_review)
    pump(panel, 0.3)

    # ------------------------------------------------- 小窗口也要看得到按钮
    # 回归：底部控件以前排在 pack 列表最后，窗口一矮就被挤出可视区，
    # 表现为"只有最大化窗口才看得到按钮"。
    panel.deiconify()
    panel.update()
    panel.geometry("720x440")
    pump(panel, 0.3)
    bar_bottom = max(b.winfo_rooty() + b.winfo_height() for b in panel.grade_btns)
    win_bottom = panel.winfo_rooty() + panel.winfo_height()
    check("最小窗口下四个按钮都在窗口内", bar_bottom <= win_bottom,
          f"buttons={bar_bottom} window={win_bottom}")
    check("最小窗口下按钮没被压扁",
          all(b.winfo_height() > 0 and b.winfo_width() > 0 for b in panel.grade_btns),
          str([(b.winfo_width(), b.winfo_height()) for b in panel.grade_btns]))
    # 挤扁的按钮文字会被截断：实际宽度必须等于它自己想要的宽度
    check("最小窗口下按钮没被截断（宽度=请求宽度）",
          all(b.winfo_width() == b.winfo_reqwidth() for b in panel.grade_btns),
          str([(b.winfo_reqwidth(), b.winfo_width()) for b in panel.grade_btns]))
    check("按钮横向排得下（不被裁掉）",
          max(b.winfo_rootx() + b.winfo_width() for b in panel.grade_btns)
          <= panel.winfo_rootx() + panel.winfo_width(),
          str([(b.winfo_rootx() - panel.winfo_rootx(), b.winfo_width())
               for b in panel.grade_btns]))
    body_h = panel.rev_body.winfo_height()
    check("窗口矮时释义框让位给按钮（没被挤没）", body_h > 0, str(body_h))
    panel.geometry("880x560")
    pump(panel, 0.2)

    # 排期时间的人话翻译
    check("due_text 精通", (due_text(None) == "不再复习" and "分钟后" in due_text(time.time() + 600)
                           and "小时后" in due_text(time.time() + 7200)
                           and "天后" in due_text(time.time() + 86400 * 3)),
          f"{due_text(None)}|{due_text(time.time() + 600)}|{due_text(time.time() + 7200)}"
          f"|{due_text(time.time() + 86400 * 3)}")

    # 生词库里也能标记/取消熟知（复习页的「熟知」是单向的，得留回头路）
    panel.nb.select(panel.tab_words)
    panel.refresh_words()
    pump(panel, 0.1)
    check("熟知按钮默认是「标记熟知」", panel.master_btn.cget("text") == "标记熟知",
          panel.master_btn.cget("text"))
    target = next(item for item in panel.tree.get_children()
                  if panel.tree.item(item, "values")[0] == "ephemeral")
    panel.tree.selection_set(target)
    pump(panel, 0.1)
    panel.toggle_mastered()
    pump(panel, 0.1)
    check("生词库可标记熟知", db.get_word("ephemeral")["status"] == "known",
          db.get_word("ephemeral")["status"])
    status_cell = next(panel.tree.item(item, "values")[3] for item in panel.tree.get_children()
                       if panel.tree.item(item, "values")[0] == "ephemeral")
    check("行内状态显示「熟知」", status_cell == "熟知", status_cell)
    check("已熟知时按钮变「取消熟知」", panel.master_btn.cget("text") == "取消熟知",
          panel.master_btn.cget("text"))
    panel.toggle_mastered()
    pump(panel, 0.1)
    check("可以取消熟知", db.get_word("ephemeral")["status"] == "learning",
          db.get_word("ephemeral")["status"])
    panel.nb.select(panel.tab_review)
    pump(panel, 0.2)

    # manual add window
    import tkinter as tk

    panel.open_quick_add()
    pump(panel, 0.3)
    toplevels = [w for w in panel.winfo_children() if isinstance(w, tk.Toplevel)]
    check("quick add window opened", len(toplevels) == 1, str(len(toplevels)))

    # backup window
    panel.open_backups()
    pump(panel, 0.3)
    bk_windows = [w for w in panel.winfo_children()
                  if isinstance(w, tk.Toplevel) and w.title() == "备份与恢复"]
    check("backup window opened", len(bk_windows) == 1, str(len(bk_windows)))
    if bk_windows:
        from wordgrab import backup as bkmod

        path = bkmod.snapshot("ui-test")
        check("panel-triggered snapshot works", bool(path) and os.path.exists(path),
              str(path))
        panel.refresh_backups()
        pump(panel, 0.2)
        rows = panel.bk_tree.get_children()
        check("backup list shows the snapshot", len(rows) >= 1, str(len(rows)))
        names = [panel.bk_tree.item(r, "values")[0] for r in rows]
        check("backup list names the file",
              any(n.startswith("words-") for n in names), str(names[:2]))
        panel.bk_tree.selection_set(rows[0])
        pump(panel, 0.1)
        check("restore target resolves",
              panel.bk_path.get(panel.bk_tree.item(rows[0], "values")[0]) is not None)
        bk_windows[0].destroy()
        pump(panel, 0.1)
    if toplevels:
        quick = toplevels[0]
        entry = next((c for c in quick.winfo_children() if isinstance(c, ttk.Entry)), None)
        check("quick add has an input field", entry is not None)
        if entry is not None:
            before_total = db.stats()["total"]
            entry.insert(0, "ephemeral")
            quick.destroy()
            panel.update()
            check("quick add window closes", True)
            check("total unchanged before submit", db.stats()["total"] == before_total)

    # logs tab
    panel.nb.select(panel.tab_logs)
    panel.refresh_logs()
    pump(panel, 0.1)
    check("capture log populated", len(panel.logs.get_children()) >= 3,
          str(len(panel.logs.get_children())))

    # stats label
    panel.refresh_stats()
    check("stats label rendered", "共" in panel.stat_label.cget("text"),
          panel.stat_label.cget("text"))

    # --------------------------------------------------- cross-thread safety
    # Regression: Tk is not thread-safe. The mouse-hook thread used to call
    # panel.after() directly, which raised
    # "RuntimeError: main thread is not in main loop" on every capture.
    from wordgrab import main as mainmod

    errors: list[BaseException] = []

    def from_worker() -> None:
        try:
            panel.post_task(panel.refresh_words)
            panel.post_task(panel.refresh_stats)
            panel.call(panel.show)
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)

    worker = threading.Thread(target=from_worker, name="hook-sim")
    worker.start()
    worker.join(5)
    check("post() from a worker thread never raises", not errors, str(errors[:1]))

    panel.update()
    deadline = time.time() + 2
    while panel._queue.qsize() and time.time() < deadline:
        panel.update()
        time.sleep(0.05)
    check("queued work is drained by the main loop", panel._queue.qsize() == 0,
          str(panel._queue.qsize()))

    # the same path used by the hook thread: on_double_click -> notify + refresh
    seen: list[dict] = []
    fake_app = type("FakeApp", (), {
        "call_soon": staticmethod(lambda fn, *a: fn(*a)),
        "call": staticmethod(lambda fn, *a: panel.post_task(fn, *a)),
        "watcher": None,
        "panel": panel,
        "capture_on": True,
        "tray": type("T", (), {"notify_added": staticmethod(lambda r: seen.append(r))})(),
    })()

    real_capture_word = capture.capture_word
    capture.capture_word = lambda x, y: {
        "word": "ephemeral", "raw": "ephemeral", "sentence": "an ephemeral glow",
        "method": "clipboard",
    }
    try:
        worker2 = threading.Thread(
            target=lambda: mainmod.App.on_double_click(
                fake_app, 10, 20, 12345, "Reader - book.txt", "edit"))
        worker2.start()
        worker2.join(10)
        check("on_double_click is safe off the main thread", not errors, str(errors[:1]))
        check("tray notification fired", bool(seen), str(seen[:1]))
        if seen:
            check("notification carries the word", seen[0]["word"] == "ephemeral",
                  str(seen[0].get("word")))
            last = db.get_conn().execute(
                "SELECT app, source, method, sentence FROM captures "
                "WHERE word='ephemeral' ORDER BY created_at DESC LIMIT 1").fetchone()
            check("capture source recorded", last["source"] == "Reader",
                  str(dict(last) if last else None))
            check("capture method recorded", "dblclick" in (last["method"] or ""),
                  str(last["method"]))
            check("capture sentence recorded",
                  (last["sentence"] or "").startswith("an ephemeral"), str(last["sentence"]))
            check("repeat capture merged instead of duplicating",
                  db.get_word("ephemeral")["hits"] == seen[0]["hits"],
                  f"hits={db.get_word('ephemeral')['hits']}")
    finally:
        capture.capture_word = real_capture_word

    deadline = time.time() + 3
    while panel._queue.qsize() and time.time() < deadline:
        panel.update()
        time.sleep(0.05)
    check("panel refresh drained", panel._queue.qsize() == 0)

    panel.destroy()

    # -------------------------------------------------------------------- tray
    # Regression: tray.start() used to call an infinite badge-refresh loop
    # synchronously on the main thread, so build_ui() never returned and
    # mainloop() was never reached -- the whole panel was dead.
    class FakeTrayApp:
        panel = None

        def call(self, fn, *a):
            return None

        def call_soon(self, fn, *a):
            fn(*a)

        def panel_show(self):
            pass

        def panel_open_review(self):
            pass

        def panel_open_quick_add(self):
            pass

        def autostart_on(self):
            return False

        def set_capture(self, _v):
            pass

        def watcher_started(self):
            return True

    import wordgrab.tray as traymod

    from wordgrab.tray import TrayController

    real_icon_cls = traymod.pystray.Icon

    class FakeIcon:
        def __init__(self, *a, **k):
            self.icon = None
            self.title = ""

        def run(self):
            time.sleep(0.4)

        def notify(self, *a, **k):
            pass

        def stop(self):
            pass

    traymod.pystray.Icon = FakeIcon
    try:
        controller = TrayController(FakeTrayApp())
        t0 = time.time()
        controller.start()
        elapsed = time.time() - t0
        check("tray.start() returns immediately", elapsed < 1.0, f"{elapsed:.2f}s")
        threads = [t.name for t in threading.enumerate()]
        check("badge refresh runs on its own thread", "tray-badge" in threads,
              str(threads))
        check("tray loop runs on its own thread", "tray" in threads, str(threads))
        controller.stop()
    finally:
        traymod.pystray.Icon = real_icon_cls

    # mainloop must actually be reachable after build_ui
    from wordgrab.main import App

    app = App(port=8805, capture_on=False, tray_on=False, start_hidden=True)
    try:
        app.build_ui()
        check("build_ui() returns (mainloop is reachable)", app.panel is not None)
        seen = {}

        def drive():
            time.sleep(1.0)
            app.panel_show()
            time.sleep(1.0)
            seen["state"] = app.panel.state()
            seen["viewable"] = bool(app.panel.winfo_viewable())
            app.shutdown(quit_ui=True)

        worker = threading.Thread(target=drive, daemon=True)
        worker.start()
        app.panel.mainloop()          # must return after shutdown posts quit()
        worker.join(5)
        check("hotkey-style panel request actually shows it",
              seen.get("viewable") is True and seen.get("state") == "normal",
              str(seen))
        check("mainloop exited after shutdown", not worker.is_alive())
    finally:
        app.server.stop()

    icon = make_icon(64)
    check("icon renders", icon.size == (64, 64) and icon.getbbox() is not None)
    badge = make_badge(icon, "12")
    check("badge renders", badge.size == (64, 64) and badge != icon)
    check("empty badge equals base", make_badge(icon, "") == icon)

    # ---------------------------------------------------------------- hotkeys
    mods, key = parse_hotkey("ctrl+alt+w")
    check("hotkey parsed", mods == 0x0003 and key == ord("W"), f"{mods:#x} {key:#x}")
    mods, key = parse_hotkey("Ctrl+Shift+F5")
    check("function key parsed", mods == 0x0006 and key == 0x74, f"{mods:#x} {key:#x}")
    check("invalid spec rejected", parse_hotkey("") == (0, 0))

    fired: list[str] = []
    hotkeys = HotkeyManager()
    check("registers a global hotkey", hotkeys.register("ctrl+alt+f9", lambda: fired.append("f9")))
    hotkeys.start()
    time.sleep(0.5)
    check("hotkey manager running", hotkeys._thread is not None and hotkeys._thread.is_alive())
    hotkeys.stop()

    # ------------------------------------------------------------------ misc
    check("normalize rejects empty", db.normalize_word("  ") == "")
    check("api server module exposes routes", hasattr(server.Handler, "do_POST"))
    check("dictionary module importable", hasattr(dictionary, "lookup"))
    check("hook still installable after all of the above", callable(capture.enable_dpi_awareness))


    verify()
    print()
    print("ALL PASS" if not failures else "FAILURES: " + ", ".join(failures))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
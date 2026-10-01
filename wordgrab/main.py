from __future__ import annotations

import argparse
import os
import sys

from . import backup, capture, db, dictionary, instance, server
from .autostart import HotkeyManager, is_autostart_enabled
from .config import HOST, PORT, load_config, save_config
from .logsetup import get_logger, log_exception
from .tray import TrayController


class App:
    def __init__(self, port: int = None, capture_on: bool = True,
                 tray_on: bool = True, start_hidden: bool = False) -> None:
        db.get_conn()
        self.capture_on = capture_on
        self.tray_on = tray_on
        self.start_hidden = start_hidden
        self.server = server.ApiServer(HOST, port or server.PORT)
        try:
            self.port = self.server.start(fallback_ports=(PORT + 1, PORT + 2, PORT + 3))
        except server.PortBusy:
            self.port = 0
            self.server.httpd = None
            get_logger().error(
                "no free port for the local API (tried %s-%s) — the browser extension "
                "will not connect; start with --port to choose another",
                PORT, PORT + 3)
        else:
            if self.port != (port or PORT):
                get_logger().warning("port %s was busy, using %s instead",
                                     port or PORT, self.port)
        self.tray = TrayController(self)
        self.hotkeys = HotkeyManager()
        self.panel = None
        self.watcher: capture.DoubleClickWatcher | None = None
        self._shutting_down = False

    # ------------------------------------------------------------- panel actions
    def panel_show(self) -> None:
        if self.panel:
            try:
                self.panel.show()
                get_logger().info(
                    "panel shown: exists=%s mapped=%s viewable=%s state=%s id=%s",
                    self.panel.winfo_exists(), self.panel.winfo_ismapped(),
                    self.panel.winfo_viewable(), self.panel.state(),
                    self.panel.winfo_id())
            except Exception:
                log_exception("panel show")

    def panel_open_review(self) -> None:
        if self.panel:
            self.panel.open_review()

    def panel_open_quick_add(self) -> None:
        if self.panel:
            self.panel.open_quick_add()

    # ------------------------------------------------------------------ wiring
    def call(self, fn, *args) -> None:
        """Run fn on the Tk thread. Safe to call from any background thread."""
        if self.panel is None:
            return
        self.panel.post_task(fn, *args)

    def call_soon(self, fn, *args) -> None:
        """Run fn on this thread, right now. Use only for Tk-free work."""
        try:
            fn(*args)
        except Exception:
            log_exception(f"background task {getattr(fn, '__name__', fn)}")

    def call_fn(self, fn):
        """Wrap a panel action so it runs on the Tk thread."""
        return lambda: self.call(fn)

    def watcher_started(self) -> bool:
        return self.watcher is not None

    def autostart_on(self) -> bool:
        return is_autostart_enabled()

    def set_capture(self, enabled: bool) -> None:
        save_config({"capture_enabled": bool(enabled)})
        self.capture_on = bool(enabled)

    # ----------------------------------------------------------------- capture
    def on_double_click(self, x: int, y: int, hwnd: int, title: str, cls: str) -> None:
        if not self.capture_on:
            return
        cfg = load_config()
        hit_result = capture.capture_word(x, y)
        if not hit_result:
            get_logger().debug("no selection captured at %s,%s (%s)", x, y, title)
            return
        source = (title.split(" - ")[0] if title else "") or cls or "unknown"
        try:
            record = db.add_word(
                hit_result["word"],
                sentence=hit_result["sentence"] or None,
                source=source,
                app=source,
                method=f"dblclick:{hit_result['method']}",
            )
        except ValueError:
            return
        get_logger().info("captured %r from %s (%s)", record["word"], source,
                          hit_result["method"])
        if cfg.get("auto_lookup", True) and not record.get("definition"):
            dictionary.enrich_in_background(record["id"], record["word"])
            found = dictionary.lookup(record["word"])
            if found:
                record["definition"] = found.get("definition")
                record["phonetic"] = found.get("phonetic")
        backup.mirror_in_background()  # cheap insurance after every capture
        if self.watcher:
            self.watcher.suppress()
        # the tray balloon needs no Tk, so notify straight from this thread
        self.call_soon(self.tray.notify_added, record)
        if self.panel:
            self.call(self.panel.refresh_words)

    # -------------------------------------------------------------------- boot
    def build_ui(self) -> None:
        from .ui import Panel

        capture.enable_dpi_awareness()
        backup.bootstrap_on_start()   # snapshot + mirror before anything else
        instance.hold(self.panel_show)
        self.panel = Panel()
        self.panel.withdraw()

        cfg = load_config()
        for name, fn in (
            ("hotkey_panel", self.panel_show),
            ("hotkey_review", self.panel_open_review),
            ("hotkey_quick_add", self.panel_open_quick_add),
        ):
            spec = cfg.get(name)
            if spec:
                self.hotkeys.register(spec, self.call_fn(fn))
        self.hotkeys.start()

        if self.capture_on:
            self.watcher = capture.DoubleClickWatcher(self.on_double_click)
            self.watcher.start()

        if self.tray_on:
            self.tray.start()

        if not self.start_hidden:
            self.panel.after(250, self.panel.show)
        self.panel.protocol("WM_DELETE_WINDOW", self.panel._hide)

    def run(self) -> int:
        self.build_ui()
        get_logger().info("entering the Tk main loop")
        try:
            self.panel.mainloop()
        except KeyboardInterrupt:
            pass
        finally:
            get_logger().info("main loop finished")
            self.shutdown(quit_ui=True)
        return 0

    def shutdown(self, quit_ui: bool = False) -> None:
        """Tear everything down. Safe from any thread.

        Background services stop here and now; the Tk call is posted to the
        main thread because panel.quit() is not thread-safe.
        """
        if self._shutting_down:
            return
        self._shutting_down = True
        get_logger().info("shutting down")
        try:
            backup.write_mirror()
        except Exception:
            log_exception("final mirror")
        if self.watcher:
            self.watcher.stop()
        self.hotkeys.stop()
        self.tray.stop()
        if self.port:
            self.server.stop()
        if quit_ui and self.panel is not None:
            self.panel.post_task(self.panel.quit)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="wordgrab", description="阅读时双击取词，自动入生词库（后台常驻）"
    )
    parser.add_argument("--minimized", action="store_true", help="启动后只留托盘图标")
    parser.add_argument("--add", metavar="WORD", help="命令行直接添加一个词并退出")
    parser.add_argument("--review", action="store_true", help="启动后直接进入复习")
    parser.add_argument("--search", metavar="QUERY", help="查询生词库并打印")
    parser.add_argument("--force", action="store_true", help="忽略缓存，重新联网取释义")
    parser.add_argument("--export", nargs="?", const="csv", metavar="FORMAT",
                        help="导出生词库: csv/tsv/json/md/txt/anki_txt")
    parser.add_argument("--out", metavar="FILE", help="导出到文件（默认打印到屏幕）")
    parser.add_argument("--status", metavar="STATUS",
                        help="配合 --export: new/learning/review/known/archived")
    parser.add_argument("--tag", metavar="TAG", help="配合 --export: 按标签筛选")
    parser.add_argument("--missing-only", action="store_true",
                        help="配合 --export: 只导出没有释义的词")
    parser.add_argument("--backup", action="store_true", help="立刻备份一次数据库")
    parser.add_argument("--backups", action="store_true", help="列出所有备份")
    parser.add_argument("--restore", metavar="FILE",
                        help="从备份恢复：给 .db 快照名/路径，或 words-mirror.json")
    parser.add_argument("--port", type=int, default=None)
    parser.add_argument("--no-capture", action="store_true", help="禁用全局双击捕获")
    parser.add_argument("--no-tray", action="store_true")
    parser.add_argument("-v", "--verbose", action="store_true", help="把日志输出到控制台")
    args = parser.parse_args(argv)

    log = get_logger(verbose=args.verbose)
    log.info("WordGrab starting (pid=%s, port=%s)", os.getpid(), args.port or PORT)

    if args.add:
        res = db.add_word(args.add, method="cli")
        hit = dictionary.lookup(res["word"], force=args.force)
        if hit:
            db.update_word(res["id"], definition=hit.get("definition"),
                           phonetic=hit.get("phonetic"), example=hit.get("example"))
        label = "新词" if res["created"] else f"已存在（第{res['hits']}次）"
        print(f"[{label}] {res['word']}  {hit['definition'] if hit else '(无释义)'}")
        return 0

    if args.search:
        for row in db.search_words(args.search, 30):
            definition = row["definition"] or "（无释义）"
            print(f"{row['word']:<20} {row['hits']:>3}次  {definition[:70]}")
        return 0

    if args.backups:
        entries = backup.list_backups()
        if not entries:
            print(f"还没有任何备份（目录：{backup.backup_dir()}）")
            return 0
        print(f"备份目录：{backup.backup_dir()}\n")
        print(f"{'文件名':<34}{'类型':<10}{'词数':>6}  {'大小':>9}  时间")
        for entry in entries:
            print(f"{entry['name']:<34}{entry['kind']:<10}"
                  f"{str(entry.get('words') or '-'):>6}  "
                  f"{entry['bytes']/1024:>8.1f}K  {entry['modified']}")
        return 0

    if args.backup:
        path = backup.snapshot("cli")
        print(f"已备份 -> {path}" if path else "备份失败（数据库不存在？）")
        return 0 if path else 1

    if args.restore:
        print("恢复会覆盖当前数据库（当前内容会先自动存一份 pre-restore 快照）。")
        answer = input(f"确定从 {args.restore} 恢复？输入 yes 继续: ").strip().lower()
        if answer != "yes":
            print("已取消")
            return 1
        try:
            result = backup.restore(args.restore)
        except FileNotFoundError as exc:
            print(f"找不到备份：{exc}")
            print("用 python -m wordgrab --backups 看看有哪些备份")
            return 1
        print(f"已恢复 {result['words']} 个词，来源：{result['source']}")
        print(f"当前统计：{result['stats']}")
        return 0

    if args.export:
        from . import export as exporter

        try:
            result = exporter.export(
                args.export, args.out, query="", status=args.status, tag=args.tag,
                missing_only=args.missing_only)
        except ValueError as exc:
            print(str(exc))
            return 2
        if result["path"]:
            print(f"已导出 {result['count']} 个词 -> {result['path']}")
        else:
            print(result["content"])
        return 0

    if args.no_capture:
        save_config({"capture_enabled": False})

    if instance.is_running():
        if instance.request_show_panel():
            log.info("another instance is already running; asked it to show its panel")
            print("WordGrab 已在运行，已把它的面板叫到前面。")
        else:
            log.warning("another instance holds the lock but did not answer")
            print("WordGrab 已在运行，但没能唤起它的面板；请看托盘图标。")
        return 0

    app = App(
        port=args.port,
        capture_on=bool(load_config().get("capture_enabled", True)),
        tray_on=not args.no_tray,
        start_hidden=args.minimized,
    )
    if args.review:
        app.start_hidden = True
        _orig_build = app.build_ui

        def build_and_open() -> None:
            _orig_build()
            app.panel.after(300, app.panel.open_review)

        app.build_ui = build_and_open  # type: ignore[method-assign]
    return app.run()


if __name__ == "__main__":
    sys.exit(main())
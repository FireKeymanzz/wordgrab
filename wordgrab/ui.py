from __future__ import annotations

import datetime
import os
import queue
import threading
import time
import tkinter as tk
from tkinter import messagebox, ttk

from . import db, dictionary, export
from .config import LOG_PATH, load_config
from .logsetup import get_logger, log_exception

BG = "#12141a"
FG = "#e8eaf0"
MUTED = "#8b93a7"
ACCENT = "#5eead4"
CARD = "#1b1f2a"
LINE = "#2a3040"

# 复习四档，顺序 = 键盘 1/2/3/4（定义在 db 里，UI 只负责显示）
GRADE_LABELS = db.LEVEL_LABELS
GRADE_KEYS = {"1": db.LEVEL_KNOWN, "2": db.LEVEL_FAMILIAR,
              "3": db.LEVEL_UNSURE, "4": db.LEVEL_UNKNOWN}
GRADE_STYLES = ("Know.TButton", "Accent.TButton", "TButton", "Miss.TButton")

# 会被抢走按键的输入控件：搜索框、手动添加、编辑释义等
EDITABLE = (tk.Entry, tk.Text, tk.Listbox, tk.Spinbox, ttk.Entry)


def due_text(due_at, now_ts: float | None = None) -> str:
    """把 due_at 说成人话，给排期反馈用。"""
    if not due_at:
        return "不再复习"
    delta = float(due_at) - (time.time() if now_ts is None else now_ts)
    if delta <= 60:
        return "马上"
    if delta < 3600:
        return f"{int(round(delta / 60))} 分钟后"
    if delta < 86400 * 1.5:
        return f"{max(1, int(round(delta / 3600)))} 小时后"
    return f"{int(round(delta / 86400))} 天后"


class Panel(tk.Tk):
    def __init__(self, on_quit=None) -> None:
        super().__init__()
        self.title("WordGrab 生词库")
        self.minsize(720, 440)
        self.configure(bg=BG)
        self.on_quit = on_quit
        self._queue: queue.Queue = queue.Queue()
        self._review_row = None
        self._last_grade: tuple[str, dict] | None = None

        self._build_styles()
        self._build_ui()
        self._fit_to_screen()
        self._alive = True
        self.after(120, self._drain_queue)
        self.bind("<Destroy>", self._on_destroy)
        self.protocol("WM_DELETE_WINDOW", self._hide)

    def _on_destroy(self, event) -> None:
        """窗口没了就别再重排定时器，否则 after 回调会报
        'invalid command name ..._drain_queue'。"""
        if event.widget is self:
            self._alive = False

    def _fit_to_screen(self) -> None:
        """别把窗口开得比屏幕工作区还高——否则底部按钮会被挤出可视区，
        表现为"只有最大化窗口才看得到按钮"（DPI 缩放下尤其明显）。"""
        try:
            width = min(880, max(720, self.winfo_screenwidth() - 80))
            height = min(560, max(440, self.winfo_screenheight() - 140))
        except tk.TclError:
            width, height = 880, 560
        self.geometry(f"{width}x{height}")
        self.minsize(min(720, width), min(440, height))

    # ------------------------------------------------------------------ style
    def _build_styles(self) -> None:
        style = ttk.Style(self)
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass
        style.configure("TFrame", background=BG)
        style.configure("Dark.TFrame", background=CARD)
        style.configure("TLabel", background=BG, foreground=FG, font=("Microsoft YaHei UI", 10))
        style.configure("Muted.TLabel", background=BG, foreground=MUTED,
                        font=("Microsoft YaHei UI", 9))
        style.configure("Card.TLabel", background=CARD, foreground=FG,
                        font=("Microsoft YaHei UI", 11, "bold"))
        style.configure("Title.TLabel", background=BG, foreground=FG,
                        font=("Microsoft YaHei UI", 16, "bold"))
        style.configure(
            "Treeview", background=CARD, fieldbackground=CARD, foreground=FG,
            rowheight=28, font=("Microsoft YaHei UI", 10), borderwidth=0,
        )
        style.map("Treeview", background=[("selected", ACCENT)],
                  foreground=[("selected", "#0b0f14")])
        style.configure("Treeview.Heading", background="#222836", foreground=MUTED,
                        font=("Microsoft YaHei UI", 9, "bold"), relief="flat")
        style.map("Treeview.Heading", background=[("active", "#2b3242")])
        style.configure("TEntry", fieldbackground="#222836", foreground=FG,
                        insertbackground=FG, font=("Microsoft YaHei UI", 11))
        style.configure("TButton", background="#262c3a", foreground=FG,
                        font=("Microsoft YaHei UI", 10), padding=(10, 6),
                        borderwidth=0)
        style.map("TButton", background=[("active", "#313949")])
        style.configure("Accent.TButton", background=ACCENT, foreground="#0b0f14",
                        font=("Microsoft YaHei UI", 10, "bold"), padding=(10, 6),
                        borderwidth=0)
        style.map("Accent.TButton", background=[("active", "#7ff0dc")])
        style.configure("Know.TButton", background="#34d399", foreground="#0b0f14",
                        font=("Microsoft YaHei UI", 10, "bold"), padding=(10, 6),
                        borderwidth=0)
        style.map("Know.TButton", background=[("active", "#6ee7b7")])
        style.configure("Miss.TButton", background="#fb7185", foreground="#1a0b10",
                        font=("Microsoft YaHei UI", 10, "bold"), padding=(10, 6),
                        borderwidth=0)
        style.map("Miss.TButton", background=[("active", "#fda4af")])
        style.configure("TNotebook", background=BG, borderwidth=0)
        style.configure("TNotebook.Tab", background="#1a1e28", foreground=MUTED,
                        padding=(16, 8), font=("Microsoft YaHei UI", 10))
        style.map("TNotebook.Tab", background=[("selected", BG)], foreground=[("selected", ACCENT)])

    # --------------------------------------------------------------------- ui
    def _build_ui(self) -> None:
        header = ttk.Frame(self)
        header.pack(fill="x", padx=18, pady=(14, 6))
        ttk.Label(header, text="WordGrab", style="Title.TLabel").pack(side="left")
        self.stat_label = ttk.Label(header, text="", style="Muted.TLabel")
        self.stat_label.pack(side="right")
        ttk.Button(header, text="今日复习", style="Accent.TButton",
                   command=self.open_review).pack(side="right", padx=8)
        ttk.Button(header, text="手动添加", command=self.open_quick_add).pack(side="right")

        self.nb = ttk.Notebook(self)
        self.nb.pack(fill="both", expand=True, padx=18, pady=(0, 14))
        self.tab_words = ttk.Frame(self.nb)
        self.tab_review = ttk.Frame(self.nb)
        self.tab_logs = ttk.Frame(self.nb)
        self.nb.add(self.tab_words, text="生词库")
        self.nb.add(self.tab_review, text="复习")
        self.nb.add(self.tab_logs, text="最近捕获")
        self._build_words_tab()
        self._build_review_tab()
        self._build_logs_tab()

    def _build_words_tab(self) -> None:
        top = ttk.Frame(self.tab_words)
        top.pack(fill="x", padx=10, pady=(12, 6))
        self.search_var = tk.StringVar()
        entry = ttk.Entry(top, textvariable=self.search_var)
        entry.pack(side="left", fill="x", expand=True)
        self.search_entry = entry
        entry.bind("<Return>", lambda _e: self.refresh_words())
        entry.bind("<KeyRelease>", lambda e: self.refresh_words() if len(e.characters) == 1 else None)
        ttk.Button(top, text="搜索", command=self.refresh_words).pack(side="left", padx=6)
        ttk.Button(top, text="补全释义", command=self.backfill).pack(side="left")
        ttk.Button(top, text="导出", command=self.open_export).pack(side="left", padx=6)
        ttk.Button(top, text="备份", command=self.open_backups).pack(side="left")
        self.master_btn = ttk.Button(top, text="标记熟知", command=self.toggle_mastered)
        self.master_btn.pack(side="left", padx=6)
        ttk.Button(top, text="删除", command=self.delete_selected).pack(side="left")

        cols = ("word", "definition", "hits", "status", "seen")
        self.tree = ttk.Treeview(self.tab_words, columns=cols, show="headings", selectmode="browse")
        self.tree.heading("word", text="单词")
        self.tree.heading("definition", text="释义")
        self.tree.heading("hits", text="遇见")
        self.tree.heading("status", text="状态")
        self.tree.heading("seen", text="最近")
        self.tree.column("word", width=170, anchor="w")
        self.tree.column("definition", width=430, anchor="w")
        self.tree.column("hits", width=60, anchor="center")
        self.tree.column("status", width=80, anchor="center")
        self.tree.column("seen", width=140, anchor="w")
        # 详情先 pack(side="bottom")：窗口再矮也是先挤表格，不会把详情/按钮挤没
        self.detail = tk.Text(self.tab_words, height=6, bg=CARD, fg=FG, relief="flat",
                              insertbackground=FG, font=("Microsoft YaHei UI", 10),
                              wrap="word", padx=10, pady=8)
        self.detail.pack(side="bottom", fill="x", padx=10, pady=(0, 10))
        self.detail.tag_configure("k", foreground=ACCENT)
        self.detail.tag_configure("m", foreground=MUTED)
        self.tree.pack(fill="both", expand=True, padx=10, pady=(0, 10))
        self.tree.bind("<Delete>", lambda _e: self.delete_selected())
        self.tree.bind("<Double-1>", lambda _e: self.edit_selected())
        self.tree.bind("<Return>", lambda _e: self.review_selected())
        self.tree.bind("<<TreeviewSelect>>", lambda _e: self.show_detail())

    def _build_review_tab(self) -> None:
        wrap = ttk.Frame(self.tab_review)
        wrap.pack(fill="both", expand=True, padx=16, pady=(18, 14))
        self.rev_wrap = wrap

        # ---- 底部区：先 pack(side="bottom")，窗口再矮也一定看得见这几行。
        # 按钮内边距压到 6：最小窗口(720)下四档 + 显示释义 才不会被挤扁。
        bar = ttk.Frame(wrap)
        bar.pack(side="bottom", fill="x", pady=(10, 0))
        ttk.Button(bar, text="显示释义(空格)", padding=(6, 6),
                   command=self.reveal).pack(side="right")
        self.grade_btns = []
        for i, label in enumerate(GRADE_LABELS):
            btn = ttk.Button(bar, text=f"{label} ({i + 1})", style=GRADE_STYLES[i],
                             padding=(6, 6), command=lambda g=i: self.grade(g))
            btn.pack(side="left", padx=(0, 6))
            self.grade_btns.append(btn)

        info = ttk.Frame(wrap)
        info.pack(side="bottom", fill="x", pady=(6, 0))
        self.rev_hint = ttk.Label(info, text="", style="Muted.TLabel", anchor="e")
        self.rev_hint.pack(side="right")
        self.rev_progress = ttk.Label(info, text="", style="Muted.TLabel", anchor="w")
        self.rev_progress.pack(side="left")

        # ---- 上部区：单词 → 音标 → 例句 → 释义框（最后 pack，窗口不够时先被压缩）
        self.rev_word = ttk.Label(wrap, text="", style="Title.TLabel")
        self.rev_word.pack(side="top", anchor="w")
        self.rev_phon = ttk.Label(wrap, text="", style="Muted.TLabel")
        self.rev_phon.pack(side="top", anchor="w", pady=(2, 10))
        self.rev_example = ttk.Label(wrap, text="", style="Muted.TLabel", wraplength=720,
                                     justify="left")
        self.rev_example.pack(side="top", fill="x", pady=(10, 0))
        self.rev_body = tk.Text(wrap, height=6, bg=CARD, fg=FG, relief="flat",
                                font=("Microsoft YaHei UI", 12), wrap="word",
                                padx=14, pady=12, takefocus=0, state="disabled")
        self.rev_body.pack(fill="both", expand=True, pady=(10, 0))
        self.rev_body.tag_configure("label", foreground=ACCENT,
                                    font=("Microsoft YaHei UI", 9, "bold"))

        # 键盘：焦点不一定落在 review 页的 Frame 上（面板刚弹出时焦点在
        # Treeview/Notebook，甚至标题栏），绑在 Frame 上等于收不到事件。
        # 所以绑两处：rev_body（先于 Text 类绑定执行）+ bind_all 兜底，
        # 再用 _wants_review_key 过滤"非复习页 / 正在输入框里打字"的情况。
        self.rev_body.bind("<KeyPress>", self._review_key)
        self.rev_body.bind("<Button-1>", self._focus_review_later)
        self.bind_all("<KeyPress>", self._review_key)
        self.nb.bind("<<NotebookTabChanged>>", self._on_tab_changed)

    # ------------------------------------------------------------ review keys
    def _on_tab_changed(self, _event=None) -> None:
        if self._review_tab_active():
            self.after_idle(self._focus_review)

    def _focus_review_later(self, _event=None) -> None:
        self.after_idle(self._focus_review)

    def _review_tab_active(self) -> bool:
        # 注意用 nb.select() 比路径：nb.tab(nb.select()) 返回的是选项字典，
        # 拿它跟路径比永远不相等（这个坑让热键弹出面板时复习页一直不刷新）。
        try:
            return self.nb.select() == str(self.tab_review)
        except tk.TclError:
            return False

    def _focus_review(self) -> None:
        """把键盘焦点交给释义框——按键先命中它，数字/空格才不会被 Text 吃掉。

        只用 focus_set（不用 focus_force），免得把用户正在用的别的窗口抢走；
        面板被 hotkey/托盘叫出来时 _show() 已经 focus_force 过，够用。
        """
        try:
            if self.state() != "withdrawn" and self._review_tab_active():
                self.rev_body.focus_set()
        except tk.TclError:
            pass

    def _review_key(self, event):
        """空格 = 看释义，1/2/3/4 = 熟知/认识/不确定/不认识。返回 'break' 吞掉事件。"""
        keysym = getattr(event, "keysym", "")
        if keysym == "space":
            level = None
        elif keysym in GRADE_KEYS:
            level = GRADE_KEYS[keysym]
        else:
            return None
        if not self._wants_review_key(event):
            return None
        if level is None:
            self.reveal()
        else:
            self.grade(level)
        return "break"

    def _wants_review_key(self, event) -> bool:
        if self._review_row is None or not self._review_tab_active():
            return False
        try:
            widget = self.nametowidget(event.widget)
        except (KeyError, tk.TclError):
            return True      # 焦点不明（标题栏/系统菜单）：照样响应
        if widget is self or widget is self.rev_body:
            return True
        if isinstance(widget, EDITABLE):
            return False     # 正在搜索框/编辑框里打字，不抢
        return self._is_within(widget, self.tab_review)   # 弹窗里的按键不抢

    @staticmethod
    def _is_within(widget, ancestor) -> bool:
        cur = widget
        while cur is not None:
            if cur is ancestor:
                return True
            cur = getattr(cur, "master", None)
        return False

    def _build_logs_tab(self) -> None:
        top = ttk.Frame(self.tab_logs)
        top.pack(fill="x", padx=10, pady=(12, 6))
        ttk.Button(top, text="刷新", command=self.refresh_logs).pack(side="left")
        cols = ("word", "source", "method", "sentence", "time")
        self.logs = ttk.Treeview(self.tab_logs, columns=cols, show="headings")
        for key, label, width in (
            ("word", "单词", 140), ("source", "来源", 170), ("method", "方式", 90),
            ("sentence", "上下文", 300), ("time", "时间", 140),
        ):
            self.logs.heading(key, text=label)
            self.logs.column(key, width=width, anchor="w")
        self.logs.pack(fill="both", expand=True, padx=10, pady=(0, 10))

    # ---------------------------------------------------------------- helpers
    def post_task(self, fn, *args) -> None:
        """Thread-safe: run fn(*args) on the Tk thread.

        Tk is not thread-safe — calling after()/createcommand() from the mouse
        hook thread raises RuntimeError. Everything goes through this queue,
        which the main thread drains from inside its event loop.

        Named post_task, not post: tkinter.Misc.post() is Tcl's vwait.
        """
        self._queue.put((fn, args))

    def _drain_queue(self) -> None:
        if not self._alive:
            return
        try:
            while True:
                task, args = self._queue.get_nowait()
                get_logger().info("panel task: %s",
                                  getattr(task, "__name__", repr(task)))
                try:
                    task(*args)
                except Exception:
                    log_exception("panel task")
        except queue.Empty:
            pass
        except tk.TclError:
            self._alive = False
            return
        try:
            self.after(150, self._drain_queue)
        except tk.TclError:
            self._alive = False

    def call(self, fn, *args) -> None:
        self.post_task(fn, *args)

    def _show(self) -> None:
        self.deiconify()
        self.lift()
        self.focus_force()

    def _hide(self) -> None:
        self.withdraw()

    def _time_str(self, ts) -> str:
        if not ts:
            return ""
        return datetime.datetime.fromtimestamp(ts).strftime("%m-%d %H:%M")

    # ---------------------------------------------------------------- actions
    def show(self) -> None:
        self._show()
        self.refresh_words()
        self.refresh_stats()
        if self._review_tab_active():
            self.next_review()
            self._focus_review()

    def toggle(self) -> None:
        if self.state() == "withdrawn":
            self.show()
        else:
            self._hide()

    def refresh_stats(self) -> None:
        s = db.stats()
        self.stat_label.configure(
            text=f"共 {s['total']} 词 · 待复习 {s['due']} · 未学 {s['new']} · 熟知 {s['known']}"
        )

    def refresh_words(self) -> None:
        rows = db.search_words(self.search_var.get().strip(), 500)
        # 重建列表会丢选中行，「标记熟知」这类操作连点两次就没了——先记住再还原
        picked = {self.tree.item(i, "values")[0] for i in self.tree.selection()}
        self.tree.delete(*self.tree.get_children())
        status_map = {"new": "未学", "learning": "学习中", "review": "复习",
                      "known": "熟知", "archived": "归档"}
        restored: list[str] = []
        for r in rows:
            defn = (r["definition"] or "").replace("\n", " ")
            item = self.tree.insert(
                "", "end",
                values=(r["word"], defn[:120], r["hits"],
                        status_map.get(r["status"], r["status"]), self._time_str(r["last_seen_at"])),
                tags=(r["word"],),
            )
            if r["word"] in picked:
                restored.append(item)
        if restored:
            self.tree.selection_set(restored)
        self._refresh_master_btn()
        self.refresh_stats()

    def _selected_row(self):
        sel = self.tree.selection()
        if not sel:
            return None
        return db.get_word(self.tree.item(sel[0], "values")[0])

    def _selected_id(self) -> int | None:
        row = self._selected_row()
        return row["id"] if row else None

    def _refresh_master_btn(self) -> None:
        """按当前选中行切换「标记熟知 / 取消熟知」——复习页的「熟知」是单向的，
        这里留个回头路，免得一个手滑就再也见不到这个词。"""
        row = self._selected_row()
        known = bool(row) and row["status"] == db.STATUS_KNOWN
        self.master_btn.configure(text="取消熟知" if known else "标记熟知")

    def toggle_mastered(self) -> None:
        word_id = self._selected_id()
        if word_id is None:
            return
        row = db.get_word_by_id(word_id)
        if row is None:
            return
        db.set_mastered(word_id, row["status"] != db.STATUS_KNOWN)
        self.refresh_words()

    def show_detail(self) -> None:
        self.detail.delete("1.0", "end")
        sel = self.tree.selection()
        if not sel:
            self._refresh_master_btn()
            return
        row = db.get_word(self.tree.item(sel[0], "values")[0])
        if row is None:
            return
        self._refresh_master_btn()
        self.detail.insert("end", row["word"], "k")
        if row["phonetic"]:
            self.detail.insert("end", f"  /{row['phonetic']}/", "m")
        self.detail.insert("end", "\n\n")
        self.detail.insert("end", row["definition"] or "（暂无释义，按 Ctrl+Alt+B 补全）", "m")
        if row["example"]:
            self.detail.insert("end", f"\n\n例：{row['example']}")
        cap = db.recent_captures(200)
        for c in cap:
            if c["word"] == row["word"] and c["sentence"]:
                self.detail.insert("end", f"\n\n出处：{c['sentence']}", "m")
                break

    def edit_selected(self) -> None:
        word_id = self._selected_id()
        if word_id is None:
            return
        row = db.get_word_by_id(word_id)
        if row is None:
            return
        win = tk.Toplevel(self)
        win.title("编辑生词")
        win.configure(bg=BG)
        win.transient(self)
        fields = {}
        for key, label in (("definition", "释义"), ("example", "例句"), ("note", "笔记"), ("tag", "标签")):
            ttk.Label(win, text=label).grid(row=len(fields) * 2, column=0, sticky="w", padx=10, pady=(8, 2))
            text = tk.Text(win, width=60, height=3, bg=CARD, fg=FG, insertbackground=FG,
                           font=("Microsoft YaHei UI", 10), relief="flat", wrap="word")
            text.grid(row=len(fields) * 2 + 1, column=0, padx=10, pady=(0, 4))
            if row[key]:
                text.insert("1.0", row[key])
            fields[key] = text
        def save() -> None:
            db.update_word(word_id, **{k: v.get("1.0", "end").strip() for k, v in fields.items()})
            win.destroy()
            self.refresh_words()
        ttk.Button(win, text="保存", style="Accent.TButton", command=save).grid(
            row=len(fields) * 2, column=0, sticky="e", padx=10, pady=10
        )

    def delete_selected(self) -> None:
        word_id = self._selected_id()
        if word_id is None:
            return
        db.delete_word(word_id)
        self.refresh_words()

    def backfill(self) -> None:
        def run() -> None:
            rows = [r for r in db.iter_words() if not r["definition"]][:100]
            for r in rows:
                hit = dictionary.lookup(r["word"])
                if hit:
                    db.update_word(r["id"], definition=hit.get("definition"),
                                   phonetic=hit.get("phonetic"), example=hit.get("example"))
                self.call(self.refresh_words)
        threading.Thread(target=run, name="backfill", daemon=True).start()

    # ---------------------------------------------------------------- export
    EXPORT_FORMATS = (
        ("md", "Markdown 生词本（Obsidian / 滴答可读）"),
        ("csv", "CSV 表格（Excel）"),
        ("tsv", "Anki 卡片（TSV，含例句出处）"),
        ("json", "JSON（完整数据，可再导入）"),
        ("txt", "纯词表（一行一个词）"),
        ("anki_txt", "Anki 基础版（词⇥释义）"),
    )

    def open_export(self) -> None:
        from tkinter import filedialog

        win = tk.Toplevel(self)
        win.title("导出生词库")
        win.configure(bg=BG)
        win.geometry("520x400")
        win.transient(self)

        ttk.Label(win, text=f"当前筛选：{self.search_var.get().strip() or '全部词'}",
                  style="Muted.TLabel").pack(anchor="w", padx=14, pady=(14, 2))
        ttk.Label(win, text="选择格式", style="Muted.TLabel").pack(anchor="w", padx=14)

        picked = tk.StringVar(value="md")
        options = tk.Frame(win, bg=BG)
        options.pack(fill="x", padx=14, pady=(4, 10))
        for fmt, label in self.EXPORT_FORMATS:
            ttk.Radiobutton(options, text=f"{label}", value=fmt, variable=picked).pack(
                anchor="w")

        only_missing = tk.BooleanVar(value=False)
        ttk.Checkbutton(win, text="只导出没有释义的词（便于补全）",
                        variable=only_missing).pack(anchor="w", padx=14)
        status = ttk.Label(win, text="", style="Muted.TLabel")
        status.pack(anchor="w", padx=14, pady=(6, 0))

        def do_export() -> None:
            fmt = picked.get()
            path = filedialog.asksaveasfilename(
                parent=win,
                title="导出到",
                defaultextension=export.FILE_SUFFIX.get(fmt, ".txt"),
                initialfile=export.suggest_filename(fmt),
                filetypes=[("所有文件", "*.*")],
            )
            if not path:
                return
            try:
                result = export.export(
                    fmt, path,
                    query=self.search_var.get().strip(),
                    missing_only=only_missing.get(),
                )
            except Exception:
                log_exception("export")
                status.configure(text=f"导出失败，详见 {LOG_PATH}")
                return
            if result["count"] == 0:
                status.configure(text="没有符合条件的词")
                return
            status.configure(text=f"已导出 {result['count']} 个词")
            self._reveal_path(result["path"], win)

        ttk.Button(win, text="导出…", style="Accent.TButton",
                   command=do_export).pack(anchor="e", padx=14, pady=(12, 14))

    def _reveal_path(self, path: str, parent) -> None:
        """Open the containing folder with the file selected."""
        if not path:
            return
        try:
            import subprocess

            subprocess.Popen(["explorer", "/select,", os.path.normpath(path)])
        except Exception:
            pass

# ------------------------------------------------------------------ backups
    def open_backups(self) -> None:
        from . import backup

        win = tk.Toplevel(self)
        win.title("备份与恢复")
        win.configure(bg=BG)
        win.geometry("660x460")
        win.transient(self)

        head = ttk.Frame(win)
        head.pack(fill="x", padx=14, pady=(14, 6))
        ttk.Label(head, text="备份与恢复",
                  style="Title.TLabel").pack(side="left")
        ttk.Button(head, text="立即备份", command=self.do_backup).pack(side="right")

        note = ttk.Label(
            win,
            text="每次启动程序会自动快照；每收录一个词会刷新 JSON 镜像。\n"
                 "选中一行再点「恢复」即可回滚（当前内容会先另存一份）。",
            style="Muted.TLabel", justify="left")
        note.pack(anchor="w", padx=14, pady=(0, 8))

        frame = ttk.Frame(win)
        frame.pack(fill="both", expand=True, padx=14)
        cols = ("name", "kind", "words", "bytes", "modified")
        self.bk_tree = ttk.Treeview(frame, columns=cols, show="headings",
                                    selectmode="browse")
        for key, label, width, anchor in (
            ("name", "文件", 250, "w"), ("kind", "类型", 80, "center"),
            ("words", "词数", 60, "center"), ("bytes", "大小", 80, "e"),
            ("modified", "时间", 130, "w"),
        ):
            self.bk_tree.heading(key, text=label)
            self.bk_tree.column(key, width=width, anchor=anchor)
        self.bk_tree.pack(fill="both", expand=True)
        self.bk_path = {"name": None}

        status = ttk.Label(win, text="", style="Muted.TLabel")
        status.pack(anchor="w", padx=14, pady=(6, 0))
        self.bk_status = status

        bar = ttk.Frame(win)
        bar.pack(fill="x", padx=14, pady=(8, 14))
        ttk.Button(bar, text="打开备份目录",
                   command=lambda: self._reveal_dir(backup.backup_dir())).pack(
            side="left")
        ttk.Button(bar, text="恢复选中项", style="Accent.TButton",
                   command=lambda: self.do_restore(win)).pack(side="right")

        self.refresh_backups()

    def refresh_backups(self) -> None:
        from . import backup

        if not hasattr(self, "bk_tree"):
            return
        self.bk_tree.delete(*self.bk_tree.get_children())
        self.bk_path = {"name": None}
        entries = backup.list_backups()
        for entry in entries:
            label = {"snapshot": "快照", "mirror": "JSON 镜像"}.get(
                entry["kind"], entry["kind"])
            self.bk_tree.insert("", "end", values=(
                entry["name"], label, entry.get("words") or "—",
                f"{entry['bytes']/1024:.1f} KB", entry["modified"]))
            self.bk_path[entry["name"]] = entry["path"]
        if not entries:
            self.bk_status.configure(text="还没有备份")

    def do_backup(self) -> None:
        from . import backup

        path = backup.snapshot("panel")
        self.refresh_backups()
        if path:
            self.bk_status.configure(text=f"已备份 -> {os.path.basename(path)}")

    def do_restore(self, win) -> None:
        from . import backup

        sel = self.bk_tree.selection()
        if not sel:
            self.bk_status.configure(text="先选中一个备份")
            return
        name = self.bk_tree.item(sel[0], "values")[0]
        path = self.bk_path.get(name)
        if not messagebox.askyesno(
            "确认恢复",
            f"确定用这个备份覆盖当前生词库吗？\n\n{name}\n\n"
            "当前内容会先自动存为一份 pre-restore 快照。",
            parent=win,
        ):
            return
        try:
            result = backup.restore(path)
        except FileNotFoundError:
            self.bk_status.configure(text=f"找不到备份：{name}")
            return
        except Exception:
            log_exception("panel restore")
            self.bk_status.configure(text="恢复失败，详见日志")
            return
        self.refresh_backups()
        self.refresh_words()
        self.bk_status.configure(
            text=f"已恢复 {result['words']} 个词，当前共 {result['stats']['total']} 个")
        messagebox.showinfo("恢复完成",
                            f"已恢复 {result['words']} 个词。", parent=win)

    def _reveal_dir(self, directory) -> None:
        try:
            os.startfile(str(directory))
        except Exception:
            pass

    # ------------------------------------------------------------------ quick add
    def open_quick_add(self) -> None:
        win = tk.Toplevel(self)
        win.title("手动添加")
        win.configure(bg=BG)
        win.geometry("520x260")
        win.transient(self)
        ttk.Label(win, text="输入单词或词组").pack(anchor="w", padx=14, pady=(14, 4))
        entry = ttk.Entry(win)
        entry.pack(fill="x", padx=14)
        entry.focus_set()
        note = tk.Text(win, height=6, bg=CARD, fg=FG, insertbackground=FG,
                       font=("Microsoft YaHei UI", 10), relief="flat", wrap="word",
                       padx=10, pady=8)
        note.pack(fill="both", expand=True, padx=14, pady=12)

        def submit() -> None:
            w = entry.get().strip()
            if not w:
                return
            text = note.get("1.0", "end").strip()
            res = db.add_word(w, note=text or None, method="manual")
            if load_config().get("auto_lookup"):
                dictionary.enrich_in_background(res["id"], res["word"])
            win.destroy()
            self.refresh_words()

        entry.bind("<Return>", lambda _e: submit())
        ttk.Button(win, text="添加", style="Accent.TButton", command=submit).pack(
            anchor="e", padx=14, pady=(0, 12)
        )

    # ------------------------------------------------------------------- review
    def open_review(self) -> None:
        self.nb.select(self.tab_review)
        self.next_review()
        self._focus_review()

    def _set_body(self, text: str, tag: str | None = None) -> None:
        """释义框是只读的（state=disabled），免得误敲的字母留在卡片上；
        写之前临时打开，写完再关回去。"""
        self.rev_body.configure(state="normal")
        self.rev_body.delete("1.0", "end")
        self.rev_body.insert("end", text, tag or ())
        self.rev_body.configure(state="disabled")

    def next_review(self) -> None:
        rows = db.review_queue(10, 30)
        s = db.stats()
        self.rev_progress.configure(
            text=f"剩余 {len(rows)} 个 · 待复习 {s['due']} · 新词 {s['new']} · 熟知 {s['known']}"
        )
        if not rows:
            self.rev_word.configure(text="没有待复习的词")
            self.rev_phon.configure(text="今天清空了，去读点东西吧")
            self._set_body("")
            self.rev_example.configure(text="")
            self._review_row = None
            return
        self._review_row = rows[0]
        row = self._review_row
        self.rev_word.configure(text=row["word"])
        self.rev_phon.configure(text=f"/{row['phonetic']}/" if row["phonetic"] else
                                f"遇见 {row['hits']} 次 · 复习 {row['reps']} 遍")
        self._set_body("（先自己回忆，按空格看释义）", "label")
        self.rev_example.configure(text=(row["example"] or "")[:220])

    def reveal(self) -> None:
        if self._review_row is None:
            return
        row = self._review_row
        self._set_body(row["definition"] or "（暂无释义）")

    def grade(self, g: int) -> None:
        if self._review_row is None:
            self.next_review()
            return
        word = self._review_row["word"]
        try:
            result = db.schedule(self._review_row["id"], g)
        except Exception:
            log_exception("review grade")
            self.rev_hint.configure(text="排期失败，详见日志")
            return
        # 告诉用户这个词被安排到什么时候了，"科学安排"才不是黑箱
        self._last_grade = (word, result)
        self.rev_hint.configure(
            text=f"「{word}」{result['label']} → {due_text(result['due_at'])}")
        self.next_review()

    def review_selected(self) -> None:
        word_id = self._selected_id()
        if word_id is None:
            return
        self._review_row = db.get_word_by_id(word_id)
        self.nb.select(self.tab_review)
        self.reveal()
        self._focus_review()

    def refresh_logs(self) -> None:
        self.logs.delete(*self.logs.get_children())
        for c in db.recent_captures(200):
            self.logs.insert(
                "", "end",
                values=(c["word"], c["source"] or c["app"] or "-",
                        c["method"] or "-", (c["sentence"] or "")[:80], self._time_str(c["created_at"])),
            )
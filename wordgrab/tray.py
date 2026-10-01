from __future__ import annotations

import threading
import time

import pystray
from PIL import Image, ImageDraw

from . import db
from .config import APP_NAME, load_config

ACCENT = (94, 234, 212)
INK = (18, 20, 26)


def make_icon(size: int = 64) -> Image.Image:
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    pad = size * 0.12
    d.rounded_rectangle(
        [pad, pad, size - pad, size - pad],
        radius=size * 0.22,
        fill=INK,
        outline=ACCENT,
        width=max(2, size // 24),
    )
    # a bookmark / word card mark
    d.rectangle(
        [size * 0.32, size * 0.26, size * 0.68, size * 0.70],
        fill=ACCENT,
    )
    d.polygon(
        [
            (size * 0.32, size * 0.70),
            (size * 0.50, size * 0.55),
            (size * 0.68, size * 0.70),
        ],
        fill=ACCENT,
    )
    return img


def make_badge(base: Image.Image, text: str) -> Image.Image:
    img = base.copy()
    d = ImageDraw.Draw(img)
    if not text:
        return img
    w = img.width
    d.ellipse([w * 0.55, w * 0.02, w * 1.02, w * 0.49], fill=(244, 114, 182))
    try:
        bbox = d.textbbox((0, 0), text)
        tw = bbox[2] - bbox[0]
    except Exception:
        tw = 6
    d.text((w * 0.785 - tw / 2, w * 0.16), text, fill=INK)
    return img


class TrayController:
    """Owns the pystray icon and exposes notification helpers.

    Menu callbacks run on pystray's thread, so anything touching Tk is handed
    to the main thread via app.call().
    """

    def __init__(self, app) -> None:
        self.app = app
        self.icon_image = make_icon()
        self.icon: pystray.Icon | None = None
        self._lock = threading.Lock()
        self._last_text = ""

    def _menu(self) -> pystray.Menu:
        return pystray.Menu(
            pystray.MenuItem(
                f"{APP_NAME} — 打开面板",
                lambda: self.app.call(self.app.panel_show),
                default=True,
            ),
            pystray.MenuItem(
                "今日复习", lambda: self.app.call(self.app.panel_open_review)
            ),
            pystray.MenuItem(
                "手动添加", lambda: self.app.call(self.app.panel_open_quick_add)
            ),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem(
                "开机自启",
                self._toggle_autostart,
                checked=lambda item: self.app.autostart_on(),
            ),
            pystray.MenuItem(
                "启用双击捕获",
                lambda item: self.app.set_capture(not item.checked),
                checked=lambda item: load_config().get("capture_enabled", True),
                enabled=lambda item: self.app.watcher_started,
            ),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("退出", lambda: self.app.call(self.app.shutdown)),
        )

    def _toggle_autostart(self, _icon=None, _item=None) -> None:
        from .autostart import is_autostart_enabled, set_autostart

        target = not is_autostart_enabled()
        set_autostart(target)
        self.refresh()

    def start(self) -> None:
        self.icon = pystray.Icon(
            APP_NAME, self.icon_image, f"{APP_NAME} 生词库", self._menu()
        )
        threading.Thread(target=self.icon.run, name="tray", daemon=True).start()
        # the badge loop must never block the caller: start() runs on the main
        # thread and the Tk panel needs it to reach mainloop()
        threading.Thread(target=self._tick, name="tray-badge", daemon=True).start()

    def _tick(self) -> None:
        while True:
            time.sleep(20)
            if self.icon is None:
                return
            try:
                due = db.stats()["due"]
                text = "99+" if due > 99 else (str(due) if due else "")
                if text != self._last_text:
                    self._last_text = text
                    self.icon.icon = make_badge(self.icon_image, text)
                    self.icon.title = f"{APP_NAME} · 待复习 {due}"
            except Exception:
                pass

    def refresh(self) -> None:
        if self.icon:
            try:
                self.icon.menu = self._menu()
                self.icon.update_menu()
            except Exception:
                pass

    def notify_added(self, result: dict) -> None:
        if not load_config().get("notify_on_add", True):
            return
        word = result["word"]
        tag = "新" if result.get("created") else f"第{result.get('hits', 1)}次"
        body = result.get("definition") or "（稍后自动补全释义）"
        body = body[:70]
        self.toast(f"已收藏 {tag}：{word}", body)

    def notify_missed(self, reason: str) -> None:
        self.toast("没有取到词", reason)

    def toast(self, title: str, message: str) -> None:
        if self.icon is None:
            return
        try:
            self.icon.notify(message, title)
        except Exception:
            pass
        if load_config().get("play_sound_on_add"):
            try:
                import winsound

                winsound.Beep(1400, 60)
            except Exception:
                pass

    def stop(self) -> None:
        if self.icon:
            self.icon.stop()
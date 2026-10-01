"""Build docs/demo-review.gif: the review flow, keyboard only.

Real widgets, real scheduler -- the frames are screen grabs of the actual
panel, driven by synthetic key events (no staging, no fake UI).

usage: python tools/make_demo_gif.py
"""
from __future__ import annotations

import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from wordgrab.capture import enable_dpi_awareness  # noqa: E402

enable_dpi_awareness()
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tests"))
from sandbox import activate  # noqa: E402

TMP = activate("wordgrab_demo_")

import ctypes  # noqa: E402
from PIL import Image, ImageDraw, ImageFont  # noqa: E402

from wordgrab import db  # noqa: E402
from wordgrab.ui import Panel  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "docs", "demo-review.gif")
CAPTION_H = 40
FPS_MS = 130

WORDS = [
    ("ephemeral", "ɪˈfiːmərəl", "adj. 短暂的；转瞬即逝的",
     "Fame is often a bubble, and the ephemeral glow of social media fades almost "
     "as quickly as it appears."),
    ("ubiquitous", "juːˈbɪkwɪtəs", "adj. 无处不在的；普遍存在的", None),
    ("lucid", "ˈluːsɪd", "adj. 清晰的；易懂的；神志清醒的", None),
    ("quintessential", "ˌkwɪntɪˈsenʃl", "adj. 典型的；精髓的", None),
    ("serendipity", "ˌserənˈdɪpəti", "n. 意外发现珍奇事物的运气；机缘巧合", None),
    ("obfuscate", "ˈɒbfjʊskeɪt", "v. 把…弄糊涂；使模糊不清", None),
    ("resilient", "rɪˈzɪliənt", "adj. 有复原力的；能迅速恢复的", None),
]
# (key, caption) -- what the viewer should notice
SCRIPT = [
    ("space", "先自己回忆一下 —— 空格看释义"),
    ("2", "2 = 认识  →  低频次再见面"),
    ("space", "空格：核对释义"),
    ("3", "3 = 不确定  →  很快会再问一次"),
    ("2", "2 = 认识"),
    ("4", "4 = 不认识  →  10 分钟后就回来"),
    ("1", "1 = 熟知  →  彻底毕业，不再出现"),
]

user32 = ctypes.windll.user32


def grab(panel: Panel) -> Image.Image:
    x, y = panel.winfo_rootx(), panel.winfo_rooty()
    w, h = panel.winfo_width(), panel.winfo_height()
    return ImageGrab.grab(bbox=(x - 8, y - 40, x + w + 8, y + h + 8))


from PIL import ImageGrab  # noqa: E402


def with_caption(img: Image.Image, text: str, font) -> Image.Image:
    out = Image.new("RGB", (img.width, img.height + CAPTION_H), "#0b0d12")
    out.paste(img, (0, 0))
    d = ImageDraw.Draw(out)
    d.text((14, img.height + 9), text, font=font, fill="#cbd5e1")
    return out


def main() -> int:
    for w, ph, de, ex in WORDS:
        db.add_word(w, phonetic=ph, definition=de, example=ex, method="demo")

    panel = Panel()
    panel.geometry("900x600")
    panel.deiconify()
    panel.lift()
    panel.attributes("-topmost", True)
    for _ in range(30):
        panel.update(); time.sleep(0.02)

    font = ImageFont.truetype(r"C:\Windows\Fonts\msyh.ttc", 19)
    small = ImageFont.truetype(r"C:\Windows\Fonts\msyh.ttc", 17)

    panel.open_review()
    for _ in range(20):
        panel.update(); time.sleep(0.02)

    frames: list[Image.Image] = []
    hold = int(1150 / FPS_MS)

    def settle(frames_n: int) -> None:
        for _ in range(frames_n):
            panel.update(); time.sleep(FPS_MS / 1000)
            frames.append(with_caption(grab(panel), current[1], font))

    current = ("", "")
    panel.reveal()
    for key, caption in SCRIPT:
        current = (key, caption)
        if key == "space":
            settle(hold)
            panel.rev_body.event_generate("<KeyPress-space>")
        else:
            panel.rev_body.event_generate(f"<KeyPress-{key}>")
        settle(hold + 1)

    # 把剩下的词也排掉，结尾才是真的"今天清空了"
    current = ("", "剩下的词也各归各位")
    guard = 0
    while panel._review_row is not None and guard < 20:
        panel.rev_body.event_generate("<KeyPress-2>")
        settle(6)
        guard += 1

    current = ("", "今天的队列清空了 —— 明天再来")
    settle(hold + 3)

    panel.attributes("-topmost", False)
    panel.destroy()

    # 缩到 720 宽：GIF 体积能小一半，文字仍然清楚
    scale = 720 / frames[0].width
    size = (720, int(frames[0].height * scale))
    frames = [f.resize(size, Image.LANCZOS) for f in frames]
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    frames[0].save(OUT, save_all=True, append_images=frames[1:], optimize=True,
                   duration=FPS_MS, loop=0, disposal=2)
    print(f"saved {OUT}  {len(frames)} frames  "
          f"{os.path.getsize(OUT) / 1024:.0f} KB  {size[0]}x{size[1]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())


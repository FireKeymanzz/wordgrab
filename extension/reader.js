const API = "http://127.0.0.1:8731";
const textEl = document.getElementById("text");
const metaEl = document.getElementById("meta");
const MAX_LEN = 40;
const LIGHT = {
  bg: "#faf8f4", fg: "#22242a", bar: "#f1ede4", line: "#e2dcd0", muted: "#8a8577",
};

let lastWord = "";
let lastAt = 0;

function setTheme(light) {
  const bg = light ? LIGHT.bg : "#12141a";
  const fg = light ? LIGHT.fg : "#e8eaf0";
  const bar = light ? LIGHT.bar : "#171b24";
  const line = light ? LIGHT.line : "#2a3040";
  const muted = light ? LIGHT.muted : "#8b93a7";
  document.body.style.background = bg;
  document.body.style.color = fg;
  document.getElementById("bar").style.background = bar;
  document.getElementById("bar").style.borderBottomColor = line;
  metaEl.style.color = muted;
}

function isWordChar(ch) {
  return /[A-Za-z0-9'’\-一-鿿]/.test(ch);
}

function showToast(word, ok) {
  const el = document.getElementById("wg-toast") || document.createElement("div");
  el.id = "wg-toast";
  el.textContent = ok ? `+ ${word}` : `! ${word}`;
  el.style.cssText = [
    "position:fixed", "right:20px", "bottom:20px", "z-index:9",
    "padding:8px 14px", "border-radius:8px",
    "font:600 13px/1.5 system-ui,sans-serif",
    ok ? "background:#12211d;color:#5eead4;border:1px solid #2f6b5f"
       : "background:#241a1a;color:#fda4af;border:1px solid #7f3a44",
    "pointer-events:none", "opacity:0", "transition:opacity .18s"
  ].join(";");
  document.body.appendChild(el);
  requestAnimationFrame(() => (el.style.opacity = "1"));
  setTimeout(() => {
    el.style.opacity = "0";
    setTimeout(() => el.remove(), 220);
  }, 1400);
}

function sentenceAround(range, word) {
  const full = textEl.innerText;
  const idx = full.indexOf(word);
  if (idx < 0) return "";
  const start = Math.max(0, idx - 90);
  const end = Math.min(full.length, idx + word.length + 90);
  return (start > 0 ? "…" : "") + full.slice(start, end).replace(/\s+/g, " ").trim()
       + (end < full.length ? "…" : "");
}

async function onDoubleClick(event) {
  const selection = window.getSelection();
  if (!selection || selection.isCollapsed || !selection.rangeCount) return;
  const range = selection.getRangeAt(0);
  let word = range.toString().trim();
  if (!word) return;
  if (!/^[A-Za-z0-9'’\-一-鿿]/.test(word)) {
    const node = range.startContainer;
    if (node.nodeType === Node.TEXT_NODE) {
      const full = node.textContent;
      let s = range.startOffset;
      let e = s + range.toString().length;
      while (s > 0 && isWordChar(full[s - 1])) s--;
      while (e < full.length && isWordChar(full[e])) e++;
      if (s < e) {
        const r = document.createRange();
        r.setStart(node, s);
        r.setEnd(node, e);
        word = r.toString().trim();
      }
    }
  }
  if (!word || word.length > MAX_LEN) return;

  const now = Date.now();
  if (word === lastWord && now - lastAt < 600) return;
  lastWord = word;
  lastAt = now;

  try {
    const res = await fetch(API + "/add", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        word,
        sentence: sentenceAround(range, word),
        source: document.title || "WordGrab 阅读器",
        method: "dblclick:reader",
        lookup: true,
      }),
    });
    if (!res.ok) throw new Error(res.status);
    const data = await res.json();
    showToast(data.word + (data.created ? "" : " (已在库)"), true);
    metaEl.textContent = `已收藏 ${data.word} · 本地共 ${(data.total ?? "")}`;
  } catch (e) {
    showToast(word + " (本地服务未启动)", false);
  }
}

document.getElementById("file").addEventListener("change", (event) => {
  const file = event.target.files[0];
  if (!file) return;
  const reader = new FileReader();
  reader.onload = () => {
    textEl.textContent = String(reader.result);
    document.title = file.name;
    metaEl.textContent = `${file.name} · ${textEl.textContent.length} 字符`;
  };
  reader.readAsText(file, "utf-8");
});

document.getElementById("theme").addEventListener("click", (e) => {
  const light = document.body.style.background === LIGHT.bg;
  setTheme(!light);
  e.target.textContent = light ? "切换主题" : "深色";
});

document.getElementById("stats").addEventListener("click", async () => {
  try {
    const res = await fetch(API + "/stats");
    const s = await res.json();
    metaEl.textContent = `共 ${s.total} 词 · 待复习 ${s.due} · 新词 ${s.new} · 24h 捕获 ${s.captured_24h}`;
  } catch (e) {
    metaEl.textContent = "本地服务未启动：请先运行 wordgrab 目录下的 start.bat";
  }
});

document.addEventListener("dblclick", onDoubleClick);
setTheme(false);
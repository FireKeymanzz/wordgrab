const API = "http://127.0.0.1:8731";
const TOAST_MS = 1400;
const MIN_LEN = 1;
const MAX_LEN = 40;
const ENABLED_KEY = "wgEnabled";

let enabled = true;
let lastWord = "";
let lastAt = 0;

function charRangeOf(node, offset) {
  const range = document.createRange();
  try {
    range.setStart(node, offset);
    range.setEnd(node, offset);
    return range;
  } catch (e) {
    return null;
  }
}

function isWordChar(ch) {
  return /[A-Za-z0-9'’\-]/.test(ch);
}

function expandToWord(range) {
  const text = range.toString();
  if (!text) return text;
  // if the double-click landed on punctuation, walk to the neighbouring word
  if (!/^[A-Za-z0-9'’\-]/.test(text)) {
    const container = range.startContainer;
    if (container.nodeType === Node.TEXT_NODE) {
      const full = container.textContent;
      let start = range.startOffset;
      let end = start + text.length;
      while (start > 0 && isWordChar(full[start - 1])) start--;
      while (end < full.length && isWordChar(full[end])) end++;
      if (start < end) {
        const r = document.createRange();
        r.setStart(container, start);
        r.setEnd(container, end);
        return r.toString();
      }
    }
  }
  return text;
}

function sentenceAround(range, word) {
  const root = range.commonAncestorContainer;
  const host = root.nodeType === Node.TEXT_NODE ? root.parentElement : root;
  if (!host || !host.innerText) return "";
  const full = host.innerText;
  const idx = full.indexOf(word);
  if (idx < 0) return "";
  const start = Math.max(0, idx - 90);
  const end = Math.min(full.length, idx + word.length + 90);
  return (start > 0 ? "…" : "") + full.slice(start, end).replace(/\s+/g, " ").trim() + (end < full.length ? "…" : "");
}

function showToast(word, ok) {
  if (document.getElementById("wg-toast")) return;
  const el = document.createElement("div");
  el.id = "wg-toast";
  el.textContent = ok ? `+ ${word}` : `! ${word}`;
  el.style.cssText = [
    "position:fixed", "right:18px", "bottom:18px", "z-index:2147483647",
    "padding:8px 14px", "border-radius:8px", "font:600 13px/1.5 system-ui,sans-serif",
    ok ? "background:#12211d;color:#5eead4;border:1px solid #2f6b5f"
       : "background:#241a1a;color:#fda4af;border:1px solid #7f3a44",
    "box-shadow:0 6px 20px rgba(0,0,0,.28)", "pointer-events:none",
    "opacity:0", "transition:opacity .18s ease"
  ].join(";");
  document.documentElement.appendChild(el);
  requestAnimationFrame(() => { el.style.opacity = "1"; });
  setTimeout(() => {
    el.style.opacity = "0";
    setTimeout(() => el.remove(), 220);
  }, TOAST_MS);
}

async function post(path, payload) {
  const res = await fetch(API + path, {
    method: "POST",
    headers: { "Content-Type": "application/json", "X-WordGrab-Source": "extension" },
    body: JSON.stringify(payload),
  });
  if (!res.ok) throw new Error("HTTP " + res.status);
  return res.json();
}

async function handle(event) {
  if (!enabled) return;
  if (event.button !== 0) return;
  if (event.defaultPrevented) return;
  const selection = window.getSelection();
  if (!selection || selection.isCollapsed || selection.rangeCount === 0) return;
  const range = selection.getRangeAt(0);
  const word = expandToWord(range).trim();
  if (!word) return;
  if (word.length < MIN_LEN || word.length > MAX_LEN) return;
  if (/^[\s\d\W_]+$/.test(word)) return;

  const now = Date.now();
  if (word === lastWord && now - lastAt < 600) return;
  lastWord = word;
  lastAt = now;

  const payload = {
    word,
    sentence: sentenceAround(range, word),
    source: location.hostname,
    url: location.href,
    title: document.title,
    method: "dblclick:extension",
    lookup: true,
  };
  try {
    const data = await post("/add", payload);
    showToast(data.word, true);
  } catch (err) {
    showToast(word + " (未连上本地服务)", false);
  }
}

document.addEventListener("dblclick", handle, true);
document.__wgInstalled = true;

chrome.storage.sync.get({ [ENABLED_KEY]: true }, (data) => {
  enabled = !!data[ENABLED_KEY];
});

chrome.storage.onChanged.addListener((changes, area) => {
  if (area === "sync" && ENABLED_KEY in changes) {
    enabled = !!changes[ENABLED_KEY].newValue;
  }
});

window.addEventListener("message", (event) => {
  if (event.source !== window) return;
  const msg = event.data;
  if (msg && msg.type === "wg-toggle") {
    enabled = msg.value;
    chrome.storage.sync.set({ [ENABLED_KEY]: enabled });
  }
});
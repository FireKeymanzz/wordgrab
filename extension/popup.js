const API = "http://127.0.0.1:8731";
const dot = document.getElementById("dot");
const state = document.getElementById("state");
const stats = document.getElementById("stats");
const toggle = document.getElementById("toggle");

async function refresh() {
  state.textContent = "检测中…";
  dot.className = "dot off";
  stats.textContent = "";
  try {
    const res = await fetch(API + "/health", { cache: "no-store" });
    if (!res.ok) throw new Error("bad status");
    const h = await res.json();
    dot.className = "dot on";
    state.textContent = "本地服务已连接";
    stats.textContent = `${h.total} 词 · 待复习 ${h.due}`;
  } catch (e) {
    dot.className = "dot off";
    state.textContent = "未连接，请先运行 start.bat";
  }
}

chrome.storage.sync.get({ wgEnabled: true }, (d) => { toggle.checked = !!d.wgEnabled; });
toggle.addEventListener("change", () => {
  chrome.storage.sync.set({ wgEnabled: toggle.checked });
});

document.getElementById("reload").addEventListener("click", refresh);
document.getElementById("copy").addEventListener("click", (e) => {
  navigator.clipboard.writeText(API).then(() => {
    e.target.textContent = "已复制 " + API;
    setTimeout(() => (e.target.textContent = "复制本地地址"), 1200);
  });
});

refresh();
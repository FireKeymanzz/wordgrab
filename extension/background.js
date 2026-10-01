const API = "http://127.0.0.1:8731";

async function ping() {
  try {
    const res = await fetch(API + "/health", { cache: "no-store" });
    if (!res.ok) return null;
    return await res.json();
  } catch (e) {
    return null;
  }
}

chrome.runtime.onMessage.addListener((msg, _sender, sendResponse) => {
  if (msg && msg.type === "wg-status") {
    ping().then((health) => sendResponse({ health }));
    return true;
  }
});

chrome.runtime.onInstalled.addListener(() => {
  chrome.storage.sync.set({ wgEnabled: true });
});

async function ensureService() {
  const health = await ping();
  if (health) return true;
  await chrome.notifications.create({
    type: "basic",
    iconUrl: chrome.runtime.getURL("icon.png"),
    title: "WordGrab 未运行",
    message: "请先启动本地服务：双击 wordgrab 目录下的 start.bat",
  });
  return false;
}

chrome.action.onClicked.addListener(ensureService);

if (chrome.commands && chrome.commands.onCommand) {
  chrome.commands.onCommand.addListener(ensureService);
}
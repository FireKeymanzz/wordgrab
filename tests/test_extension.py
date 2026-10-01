"""Exercise the extension's capture logic against the running WordGrab service.

The content script and reader script are plain DOM code, so we serve real pages
over HTTP, drive them in Chromium (Playwright), and double-click the actual
bounding box of a chosen word.
"""

from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import threading
import time
import urllib.request
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
from sandbox import activate, verify  # noqa: E402

TMP = activate("wordgrab_ext_")

sys.path.insert(0, ROOT)
from wordgrab import server  # noqa: E402

API_PORT = 8803
WEB_PORT = 8804
failures: list[str] = []

INDEX_HTML = """<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>Reading Test - Chapter 4</title></head>
<body style="font:20px Georgia,serif">
  <h1>Chapter 4</h1>
  <p id="para">The ephemeral glow faded quickly today.</p>
  <p id="para2">alpha, beta gamma.</p>
</body></html>
"""

SHIM = """
const chrome = {
  storage: { sync: { get: (d, cb) => cb({ wgEnabled: true }),
                    set: (v) => { window.__wgStorage = { ...(window.__wgStorage||{}), ...v }; },
                    onChanged: { addListener: () => {} } } },
};
window.__wgStorage = { wgEnabled: true };
"""

WORD_RECT_JS = """
([selector, word]) => {
  const host = document.querySelector(selector);
  const textNode = [...host.childNodes].find(n => n.nodeType === Node.TEXT_NODE);
  const full = textNode.textContent;
  const idx = full.indexOf(word);
  if (idx < 0) return null;
  const r = document.createRange();
  r.setStart(textNode, idx);
  r.setEnd(textNode, idx + word.length);
  const box = r.getBoundingClientRect();
  return { x: box.x + box.width / 2, y: box.y + box.height / 2, width: box.width };
}
"""


def check(name: str, cond: bool, extra: str = "") -> None:
    print(f"{'PASS' if cond else 'FAIL'}  {name}{(' :: ' + extra) if extra else ''}")
    if not cond:
        failures.append(name)


def api(path: str, payload=None, method: str = "GET"):
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(
        f"http://127.0.0.1:{API_PORT}{path}", data=data, method=method,
        headers={"Content-Type": "application/json"} if data else {})
    with urllib.request.urlopen(req, timeout=10) as resp:
        return json.loads(resp.read().decode("utf-8"))


def patched(path: str, port: int) -> str:
    text = open(os.path.join(ROOT, "extension", path), encoding="utf-8").read()
    return text.replace('const API = "http://127.0.0.1:8731";',
                        f'const API = "http://127.0.0.1:{port}";')


def main() -> int:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("SKIP  playwright missing (pip install playwright && playwright install chromium)")
        verify()
        return 0

    web_dir = tempfile.mkdtemp(prefix="wordgrab_web_")
    with open(os.path.join(web_dir, "index.html"), "w", encoding="utf-8") as fh:
        fh.write(INDEX_HTML)
    for name in ("content.js", "reader.html", "reader.js", "popup.html", "popup.js"):
        with open(os.path.join(ROOT, "extension", name), encoding="utf-8") as src:
            body = patched(name, API_PORT) if name.endswith(".js") else src.read()
        if name == "popup.html":
            # the page already loads popup.js on its own; drop that tag so the
            # test can inject the chrome shim first and load the script once
            body = body.replace('<script src="popup.js"></script>', "")
        with open(os.path.join(web_dir, name), "w", encoding="utf-8") as dst:
            dst.write(body)

    handler = partial(SimpleHTTPRequestHandler, directory=web_dir)
    handler.log_message = lambda *a, **k: None
    webd = ThreadingHTTPServer(("127.0.0.1", WEB_PORT), handler)
    webd.daemon_threads = True
    threading.Thread(target=webd.serve_forever, daemon=True).start()

    srv = server.ApiServer("127.0.0.1", API_PORT)
    srv.start()

    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch()
            page = browser.new_page()
            page.goto(f"http://127.0.0.1:{WEB_PORT}/index.html")
            page.add_script_tag(content=SHIM)
            page.add_script_tag(content=patched("content.js", API_PORT))
            check("content script installed",
                  page.evaluate("typeof document.__wgInstalled !== 'undefined'"))

            rect = page.evaluate(WORD_RECT_JS, ["#para", "ephemeral"])
            check("found the word on the page", rect is not None and rect["width"] > 0,
                  str(rect))
            page.mouse.dblclick(rect["x"], rect["y"])
            time.sleep(1.5)

            words = api("/words?limit=20")["words"]
            check("extension double click stored the word",
                  any(w["word"].lower() == "ephemeral" for w in words),
                  str([w["word"] for w in words]))

            caps = api("/captures?limit=5")["captures"]
            check("extension recorded the method",
                  any("extension" in (c["method"] or "") for c in caps),
                  str([c["method"] for c in caps[:2]]))
            check("extension recorded the page url",
                  any("index.html" in (c["url"] or "").lower() for c in caps),
                  str([c["url"] for c in caps[:2]]))
            check("extension recorded the source host",
                  any("127.0.0.1" in (c["source"] or "") for c in caps),
                  str([c["source"] for c in caps[:2]]))
            check("extension captured the sentence",
                  any(c["sentence"] and "ephemeral" in c["sentence"] for c in caps),
                  str([c["sentence"] for c in caps[:2]]))

            page.mouse.dblclick(rect["x"], rect["y"])
            time.sleep(1.5)
            words2 = api("/words?limit=20")["words"]
            eph = [w for w in words2 if w["word"].lower() == "ephemeral"]
            check("repeat capture merges into one row",
                  len(eph) == 1 and eph[0]["hits"] == 2,
                  f"rows={len(eph)} hits={eph[0]['hits'] if eph else None}")

            # double clicking on the comma must resolve the neighbouring word
            comma = page.evaluate("""
              () => {
                const t = document.querySelector('#para2').childNodes[0];
                const r = document.createRange();
                r.setStart(t, 5); r.setEnd(t, 6);
                const b = r.getBoundingClientRect();
                return { x: b.x + b.width / 2, y: b.y + b.height / 2 };
              }
            """)
            page.mouse.dblclick(comma["x"], comma["y"])
            time.sleep(1.5)
            words3 = [w["word"] for w in api("/words?limit=20")["words"]]
            check("punctuation click resolves the neighbouring word",
                  any(w.lower().strip(" ,.;") in ("alpha", "beta") for w in words3),
                  str(words3))
            check("comma itself not stored", not any(w.strip() == "," for w in words3),
                  str(words3))

            # reader page
            reader = browser.new_page()
            reader.goto(f"http://127.0.0.1:{WEB_PORT}/reader.html")
            reader.add_script_tag(content=SHIM)
            reader.add_script_tag(content=patched("reader.js", API_PORT))
            reader.evaluate(
                "document.getElementById('text').textContent = "
                "'A resilient design tolerates failure quietly.'")
            rrect = reader.evaluate(WORD_RECT_JS, ["#text", "resilient"])
            check("reader found the word", rrect is not None, str(rrect))
            reader.mouse.dblclick(rrect["x"], rrect["y"])
            time.sleep(1.5)
            words4 = [w["word"] for w in api("/words?limit=30")["words"]]
            check("reader double click stored the word",
                  any(w.lower() == "resilient" for w in words4), str(words4))
            caps4 = api("/captures?limit=5")["captures"]
            check("reader recorded the method",
                  any("reader" in (c["method"] or "") for c in caps4),
                  str([c["method"] for c in caps4[:2]]))

            # popup reflects live stats
            popup = browser.new_page()
            popup.on("console", lambda m: print("   popup console:", m.type, m.text))
            popup.on("pageerror", lambda e: print("   popup error:", e))
            popup.goto(f"http://127.0.0.1:{WEB_PORT}/popup.html")
            popup.add_script_tag(content=SHIM)
            popup.add_script_tag(content=patched("popup.js", API_PORT))
            fetch_state = popup.evaluate("""
              async () => {
                try {
                  const r = await fetch('%s/health');
                  return 'status=' + r.status + ' cors=' + r.headers.get('access-control-allow-origin');
                } catch (e) { return 'fetch failed: ' + e; }
              }
            """ % f"http://127.0.0.1:{API_PORT}")
            check("popup page can reach the API", "status=200" in fetch_state, fetch_state)
            try:
                popup.wait_for_function(
                    "document.getElementById('state').textContent.includes('已连接')",
                    timeout=8000)
                connected = True
            except Exception:
                connected = False
            state_text = popup.inner_text("#state")
            check("popup detects the local service", connected, state_text)
            check("popup shows live stats", "词" in popup.inner_text("#stats"),
                  popup.inner_text("#stats"))

            popup.click("#toggle")
            time.sleep(0.3)
            check("popup toggle persists",
                  popup.evaluate("window.__wgStorage.wgEnabled") is False,
                  str(popup.evaluate("window.__wgStorage")))

            # service down -> toast instead of an unhandled error
            srv.stop()
            time.sleep(0.5)
            other = page.evaluate(WORD_RECT_JS, ["#para2", "gamma"])
            check("still have a target word", other is not None, str(other))
            page.mouse.dblclick(other["x"], other["y"])
            page.wait_for_selector("#wg-toast", timeout=5000)
            toast = page.locator("#wg-toast")
            check("toast shown when service is down", toast.count() >= 1,
                  str(toast.count()))
            if toast.count():
                check("toast explains the failure",
                      "未连上" in toast.inner_text(), toast.inner_text())
            check("page still alive", page.evaluate("1 + 1") == 2)

            browser.close()
    finally:
        srv.stop()
        webd.shutdown()
        shutil.rmtree(web_dir, ignore_errors=True)
        verify()

    print()
    print("ALL PASS" if not failures else "FAILURES: " + ", ".join(failures))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
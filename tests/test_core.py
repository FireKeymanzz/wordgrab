from __future__ import annotations

import json
import os
import sys
import time
import urllib.error
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sandbox import activate, verify  # noqa: E402

TMP = activate("wordgrab_test_")

from wordgrab import capture, db, server  # noqa: E402

FAIL: list[str] = []


def check(name: str, cond: bool, extra: str = "") -> None:
    print(f"{'PASS' if cond else 'FAIL'}  {name}{(' :: ' + extra) if extra else ''}")
    if not cond:
        FAIL.append(name)


# ---------------------------------------------------------------- normalize
check("normalize trims punctuation", db.normalize_word('"ephemeral,"') == "ephemeral",
      db.normalize_word('"ephemeral,"'))
check("normalize collapses phrase to word", db.normalize_word("ephemeral life") == "ephemeral")
check("normalize keeps CJK run", db.normalize_word("短暂") == "短暂")
check("normalize handles fullwidth", db.normalize_word("ＡＢＣ") == "ABC")
check("empty rejected", db.normalize_word("   ") == "")

# ------------------------------------------------------------------- add/merge
r1 = db.add_word("ephemeral", definition="adj. 短暂的", method="test")
check("first add is new", r1["created"] is True)
r2 = db.add_word("Ephemeral", sentence="The ephemeral glow faded.", method="test")
check("duplicate merges (case-insensitive)", r2["created"] is False and r2["hits"] == 2,
      f"hits={r2['hits']}")
check("definition preserved on merge", r2["definition"] == "adj. 短暂的")
check("only one row", len(db.search_words("ephemeral")) == 1)

r3 = db.add_word("ephemeral", definition=None, method="test")
row = db.get_word("ephemeral")
check("None definition does not wipe", row["definition"] == "adj. 短暂的")

db.add_word("resilient", definition="adj. 有复原力的")
db.add_word("banana", definition="n. 香蕉")
check("search by definition", len(db.search_words("香蕉")) == 1)
check("search by word", len(db.search_words("banana")) == 1)

# ------------------------------------------------------------------- sentence
frag = db.sentence_around(
    "In the long run this ephemeral arrangement proved unsustainable and had to be rebuilt "
    "from the ground up, which surprised everyone involved in the project.", "ephemeral", 60)
check("sentence contains word", "ephemeral" in frag)
check("sentence truncated with ellipsis", len(frag) <= 64 and frag.endswith("…"), frag[-20:])
check("sentence is single line", "\n" not in frag)
check("sentence short input has no ellipsis",
      db.sentence_around("an ephemeral day", "ephemeral", 160) == "an ephemeral day")

# ------------------------------------------------------------------ scheduling
# 0 熟知 / 1 认识 / 2 不确定 / 3 不认识
check("level names parse", db.parse_level("认识") == db.LEVEL_FAMILIAR
      and db.parse_level(3) == db.LEVEL_UNKNOWN and db.parse_level("0") == db.LEVEL_KNOWN)
try:
    db.parse_level("乱七八糟")
    check("bad level rejected", False)
except ValueError:
    check("bad level rejected", True)

wid = r1["id"]
s0 = db.schedule(wid, 1)  # 认识 -> 低频次起步
check("认识 -> 3 天", 2.9 <= s0["interval_days"] <= 3.1, str(s0["interval_days"]))
s1 = db.schedule(wid, 1)
check("再认识 -> 一周多", 6.9 <= s1["interval_days"] <= 8.1, str(s1["interval_days"]))
s2 = db.schedule(wid, 1)
check("认识按 ease 拉长", s2["interval_days"] > s1["interval_days"] * 2,
      f"{s1['interval_days']}->{s2['interval_days']}")
s3 = db.schedule(wid, 1)
check("稳定期 status=review", s3["status"] == "review" and s3["interval_days"] > 30,
      f"{s3['status']} {s3['interval_days']}d")

# 不确定：间隔砍到 40% 以内，最多 3 天
u1 = db.schedule(wid, 2)
check("不确定砍间隔", 2.9 <= u1["interval_days"] <= 3.0, str(u1["interval_days"]))
check("不确定留在学习中", u1["status"] == "learning")
u2 = db.schedule(wid, 2)
check("再不确定继续缩短", u2["interval_days"] < u1["interval_days"],
      f"{u1['interval_days']}->{u2['interval_days']}")
db.add_word("unsurenew", definition="半懂")
un = db.get_word("unsurenew")["id"]
u3 = db.schedule(un, 2)
check("新词判不确定 -> 半天后再见", 0.4 <= u3["interval_days"] <= 0.6,
      str(u3["interval_days"]))
u4 = db.schedule(un, 2)
check("反复不确定压到当天内", 0.19 <= u4["interval_days"] <= 0.25, str(u4["interval_days"]))
u5 = db.schedule(un, 2)
check("不确定有下限，不会当天无限刷", u5["interval_days"] >= 0.19, str(u5["interval_days"]))

# 不认识：进重学阶梯，间隔以分钟计
before = db.get_word_by_id(wid)
n0 = db.schedule(wid, 3)
row0 = db.get_word_by_id(wid)
check("不认识重置 reps", row0["reps"] == 0 and row0["lapses"] == before["lapses"] + 1,
      f"reps={row0['reps']} lapses={row0['lapses']}")
check("不认识降低 ease", row0["ease"] < before["ease"])
check("不认识 10 分钟后再来", time.time() + 540 <= row0["due_at"] <= time.time() + 660,
      str(row0["due_at"] - time.time()))
n1 = db.schedule(wid, 3)
check("再次不认识继续下探阶梯", n1["interval_days"] > n0["interval_days"],
      f"{n0['interval_days']}->{n1['interval_days']}")
check("不认识始终以小时/分钟计", n1["interval_days"] <= 2.0, str(n1["interval_days"]))

for _ in range(30):
    db.schedule(wid, 1)
check("认识封顶 3 年", db.get_word_by_id(wid)["interval_days"] <= 365 * 3 + 1,
      str(db.get_word_by_id(wid)["interval_days"]))

# 熟知：彻底退出队列
k = db.schedule(wid, 0)
krow = db.get_word_by_id(wid)
check("熟知 -> status known", krow["status"] == "known", krow["status"])
check("熟知 -> 不再排期", krow["due_at"] is None and k["due_at"] is None)
check("熟知 -> 不在待复习队列", not any(x["word"] == "ephemeral" for x in db.due_words(50)))
db.get_conn().execute("UPDATE words SET due_at=? WHERE id=?", (time.time() - 1, wid))
db.get_conn().commit()
check("即使 due 过期也不复习", not any(x["word"] == "ephemeral" for x in db.due_words(50)))
check("熟知计入统计", db.stats()["known"] >= 1)
back = db.set_mastered(wid, False)
check("取消熟知能回到队列", back["status"] == "learning" and back["due_at"] is not None
      and back["interval_days"] <= 3.0, str(back))
db.get_conn().execute("UPDATE words SET due_at=? WHERE id=?", (time.time() - 1, wid))
db.get_conn().commit()
check("取消熟知后重新到期", any(x["word"] == "ephemeral" for x in db.due_words(50)))

db.add_word("reviewme", definition="待复习")
rid = db.get_word("reviewme")["id"]
db.schedule(rid, 3)
row = db.get_word_by_id(rid)
check("不认识 -> status learning", row["status"] == "learning")
check("不认识 -> 10 分钟内到期", time.time() + 500 <= row["due_at"] <= time.time() + 700)
check("relearn not yet due", not any(x["word"] == "reviewme" for x in db.due_words(50)))
db.get_conn().execute("UPDATE words SET due_at=? WHERE id=?", (time.time() - 1, rid))
db.get_conn().commit()
check("relearn enters due queue once due",
      any(x["word"] == "reviewme" for x in db.due_words(50)))
check("review queue non-empty", len(db.review_queue(50, 50)) > 0)
check("due queue 按 due_at 升序（最该回顾的排最前）",
      [r["due_at"] for r in db.due_words(20)] == sorted(r["due_at"] for r in db.due_words(20)))

# ------------------------------------------------------------------- schedule API
srv = server.ApiServer("127.0.0.1", 8799)
port = srv.start()


def api(path: str, payload=None, method: str = "GET"):
    url = f"http://127.0.0.1:8799{path}"
    if payload is None:
        req = urllib.request.Request(url, method=method)
    else:
        req = urllib.request.Request(
            url, data=json.dumps(payload).encode(),
            headers={"Content-Type": "application/json"}, method=method)
    with urllib.request.urlopen(req, timeout=10) as resp:
        return json.loads(resp.read().decode("utf-8"))

health = api("/health")
check("health ok", health["ok"] is True and health["total"] >= 4, json.dumps(health))

added = api("/add", {"word": "ubiquitous", "sentence": "Smartphones are ubiquitous.",
                     "source": "test", "method": "test"}, "POST")
check("POST /add", added["ok"] and added["created"] is True)
again = api("/add", {"word": "Ubiquitous", "method": "test"}, "POST")
check("POST /add merges", again["created"] is False and again["hits"] == 2)

check("GET /words", len(api("/words?q=ubiquit")["words"]) == 1)
listed = api("/words")
check("GET /words lists all", len(listed["words"]) >= 5)
by_id = api(f"/words/{added['id']}")
check("GET /words/:id", by_id["word"]["word"] == "ubiquitous")
queue = api("/review/queue")
check("GET /review/queue", isinstance(queue["queue"], list))
check("GET /review/queue 公布四档",
      [lv["label"] for lv in queue["levels"]] == ["熟知", "认识", "不确定", "不认识"],
      str(queue.get("levels")))
check("GET /stats", api("/stats")["total"] >= 5)
check("GET /captures", len(api("/captures?limit=5")["captures"]) > 0)

upd = api("/review/%d" % added["id"], {"level": 1}, "POST")
check("POST /review", upd["ok"] and upd["level"] == 1 and upd["label"] == "认识", str(upd))
upd2 = api("/review/%d" % added["id"], {"grade": "不认识"}, "POST")
check("POST /review 接受中文档位", upd2["ok"] and upd2["level"] == 3, str(upd2))
check("POST /words/update", api("/words/update", {"id": added["id"], "note": "手工笔记"}, "POST")["ok"])
check("note saved", db.get_word_by_id(added["id"])["note"] == "手工笔记")


def api_error(path: str, payload=None, method: str = "GET"):
    try:
        api(path, payload, method)
        return None
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read().decode("utf-8"))


err = api_error("/nope")
check("bad route 404", err[0] == 404 and err[1]["error"] == "not found")
check("empty word rejected", api_error("/add", {"word": ""}, "POST")[0] == 400)
check("bad review level rejected",
      api_error("/review/%d" % added["id"], {"level": 9}, "POST")[0] == 400)

# CORS preflight
req = urllib.request.Request("http://127.0.0.1:8799/add", method="OPTIONS")
with urllib.request.urlopen(req, timeout=10) as resp:
    check("CORS preflight", resp.status == 204 and
          resp.headers["Access-Control-Allow-Origin"] == "*")
srv.stop()

# ------------------------------------------------------------------- capture
check("capture module loads", callable(capture.capture_word))
check("clipboard snapshot works", capture.Clipboard().snapshot() is None)


def dummy(*a, **k):
    return None


watcher = capture.DoubleClickWatcher(dummy)
watcher.start()
time.sleep(0.6)
check("hook installs", watcher._hook_id is not None, str(watcher._hook_id))
watcher.stop()


verify()

print()
print(f"{'ALL PASS' if not FAIL else 'FAILURES: ' + ', '.join(FAIL)}")
sys.exit(1 if FAIL else 0)
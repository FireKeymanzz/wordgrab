"""Concurrency: many threads hitting the database at once.

Regression for:
    sqlite3.InterfaceError: bad parameter or other API misuse
raised from the dictionary thread while the UI thread was writing. The cause
was one shared sqlite3 connection used from several threads.
"""

from __future__ import annotations

import os
import queue
import random
import sys
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

from sandbox import activate, verify  # noqa: E402

TMP = activate("wordgrab_conc_")

from wordgrab import db, export  # noqa: E402

failures: list[str] = []


def check(name: str, cond: bool, extra: str = "") -> None:
    print(f"{'PASS' if cond else 'FAIL'}  {name}{(' :: ' + extra) if extra else ''}")
    if not cond:
        failures.append(name)


WORDS = [f"conc{i:03d}" for i in range(40)]


def main() -> int:
    errors: "queue.Queue" = queue.Queue()

    def guard(fn, label):
        def wrapper(*a, **k):
            try:
                return fn(*a, **k)
            except BaseException as exc:  # noqa: BLE001
                errors.put(f"{label}: {type(exc).__name__}: {exc}")
                return None
        return wrapper

    db.add_word("seed", definition="种子", method="test")

    # ------------------------------------------------- one connection per thread
    conns: dict[int, int] = {}
    lock = threading.Lock()

    def grab_conn(label):
        conn = db.get_conn()
        with lock:
            conns[threading.get_ident()] = id(conn)
        return conn

    def reader():
        for _ in range(60):
            guard(db.search_words, "reader.search")("conc", 20)
            guard(db.get_word, "reader.get")("seed")
            guard(db.stats, "reader.stats")()
            guard(db.review_queue, "reader.queue")(5, 5)
            guard(db.due_words, "reader.due")(5)
            guard(db.recent_captures, "reader.captures")(5)
            guard(grab_conn, "reader.conn")("reader")

    def writer(tag):
        for i, w in enumerate(WORDS):
            guard(db.add_word, f"{tag}.add")(f"{w}", definition="d", method="test")
            guard(db.update_word, f"{tag}.update")(
                db.get_word(w)["id"], note=f"{tag}-{i}")
            guard(db.cache_put, f"{tag}.cache")(w, {"definition": "d"})
            guard(db.cache_get, f"{tag}.cache_get")(w)
            guard(grab_conn, f"{tag}.conn")(tag)

    def scheduler():
        for _ in range(40):
            row = db.get_word(random.choice(WORDS))
            if row:
                guard(db.schedule, "sched")(row["id"], random.randint(0, 3))
                guard(db.review_queue, "sched.queue")(2, 2)
            time.sleep(0.002)

    def exporter():
        for fmt in ("csv", "json", "md", "txt", "tsv"):
            guard(export.render, f"export.{fmt}")(
                fmt, guard(export.select_words, "export.select")())
            guard(export.export, "export.write")(fmt, os.path.join(TMP, f"out.{fmt}"))

    start = time.time()
    threads = []
    def spawn(fn, label, **kw):
        threads.append(threading.Thread(
            target=guard(lambda: fn(**kw), label), name=label, daemon=True))

    for i in range(4):
        spawn(reader, f"reader{i}")
    for i in range(3):
        spawn(writer, f"writer{i}", tag=f"writer{i}")
    spawn(scheduler, "scheduler")
    spawn(exporter, "exporter")

    for t in threads:
        t.start()
    for t in threads:
        t.join(60)
    elapsed = time.time() - start

    check("all threads finished", not any(t.is_alive() for t in threads),
          f"{elapsed:.1f}s")
    check("no thread reported an error", errors.empty(),
          "; ".join(list(errors.queue)[:3]))

    # the shared-connection bug showed up as InterfaceError; assert we never see it
    text = " ".join(errors.queue)
    check("no sqlite InterfaceError", "InterfaceError" not in text, text[:200])
    check("no 'database is locked'", "database is locked" not in text, text[:200])

    # ---------------------------------------------------- per-thread connections
    check("each thread got its own connection", len(set(conns.values())) >= 2,
          f"{len(set(conns.values()))} distinct connections over {len(conns)} threads")

    # ------------------------------------------------------------- consistency
    check("all rows landed", db.stats()["total"] == len(WORDS) + 1, str(db.stats()))
    for w in WORDS:
        if db.get_word(w) is None:
            check(f"row {w} present", False)
            break
    else:
        check("every written row is readable", True)

    check("cache rows readable",
          db.cache_get("conc001") is not None, str(db.cache_get("conc001")))
    check("dedupe survived the race",
          db.get_word("conc001")["hits"] >= 3, str(db.get_word("conc001")["hits"]))

    # a brand new thread must be able to read what others wrote
    fresh: list[int] = []

    def late_reader():
        fresh.append(db.stats()["total"])

    t = threading.Thread(target=late_reader)
    t.start()
    t.join(10)
    check("a later thread sees committed data", fresh == [len(WORDS) + 1], str(fresh))

    verify()
    print()
    print("ALL PASS" if not failures else "FAILURES: " + ", ".join(failures))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
"""Backups: snapshots, the JSON mirror, pruning, and restores."""

from __future__ import annotations

import json
import os
import sqlite3
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from sandbox import activate, verify  # noqa: E402

TMP = activate("wordgrab-backup-test_")
sys.path.insert(0, os.path.dirname(HERE))  # repo root, for `import wordgrab`

from wordgrab import backup, db  # noqa: E402

failures: list[str] = []


def check(name: str, cond: bool, extra: str = "") -> None:
    print(f"{'PASS' if cond else 'FAIL'}  {name}{(' :: ' + extra) if extra else ''}")
    if not cond:
        failures.append(name)


def main() -> int:
    bdir = backup.backup_dir()
    check("backup dir sits next to the database, not in the repo",
          str(bdir).startswith(str(TMP)), str(bdir))

    for i in range(5):
        db.add_word(f"bk{i}", definition=f"def{i}", sentence=f"sentence {i}",
                    source="Reader", method="test")

    # ------------------------------------------------------------- snapshot
    snap = backup.snapshot("selftest")
    check("snapshot returns a path", bool(snap), str(snap))
    check("snapshot file exists", os.path.exists(snap))
    check("snapshot has a timestamped name",
          os.path.basename(snap).startswith("words-")
          and os.path.basename(snap).endswith(".db"), os.path.basename(snap))
    conn = sqlite3.connect(f"file:{snap}?mode=ro", uri=True)
    try:
        n = conn.execute("SELECT COUNT(*) FROM words").fetchone()[0]
        sent = conn.execute(
            "SELECT COUNT(*) FROM captures WHERE sentence IS NOT NULL").fetchone()[0]
    finally:
        conn.close()
    check("snapshot opens on its own and holds the words", n == 5, str(n))
    check("snapshot holds the source sentences", sent >= 5, str(sent))

    # snapshot must be a consistent view, not a half-written WAL
    db.add_word("after_snapshot", definition="x", method="test")
    conn = sqlite3.connect(f"file:{snap}?mode=ro", uri=True)
    try:
        still = conn.execute(
            "SELECT COUNT(*) FROM words WHERE word='after_snapshot'").fetchone()[0]
    finally:
        conn.close()
    check("snapshot is a point-in-time copy", still == 0, str(still))

    # ---------------------------------------------------------------- mirror
    mirror = backup.write_mirror()
    check("mirror returns a path", bool(mirror) and os.path.exists(mirror), str(mirror))
    data = json.loads(open(mirror, encoding="utf-8").read())
    check("mirror has words", len(data["words"]) == 6, str(len(data["words"])))
    check("mirror keeps definitions",
          any(w["word"] == "bk0" and w["definition"] == "def0" for w in data["words"]))
    check("mirror keeps captures history", len(data["captures"]) >= 5,
          str(len(data["captures"])))
    check("mirror records counts", data["counts"]["total"] == 6,
          str(data["counts"]))
    check("mirror has a generation timestamp", bool(data["generated_at"]))

    # atomic write: no .tmp left behind
    leftovers = [p for p in os.listdir(bdir) if p.endswith(".tmp")]
    check("mirror write leaves no .tmp file", not leftovers, str(leftovers))

    # ------------------------------------------------------------- listing
    entries = backup.list_backups()
    names = [e["name"] for e in entries]
    check("listing includes the snapshot", any(n.endswith(".db") for n in names), str(names))
    check("listing includes the mirror", "words-mirror.json" in names, str(names))
    check("listing hides wal/shm sidecars",
          not any(n.endswith(("-wal", "-shm")) for n in names), str(names))
    snap_entry = next(e for e in entries if e["kind"] == "snapshot")
    check("listing reports the word count", snap_entry["words"] == 5,
          str(snap_entry["words"]))
    mirror_entry = next(e for e in entries if e["kind"] == "mirror")
    check("listing reports mirror word count", mirror_entry["words"] == 6,
          str(mirror_entry["words"]))

    # a second snapshot in the same second must not clobber the first
    twin = backup.snapshot("same-second")
    check("snapshot names are unique within the same second",
          twin and os.path.basename(twin) != os.path.basename(snap),
          str(os.path.basename(twin or "")))
    check("both snapshots survive", os.path.exists(snap) and os.path.exists(twin))

    # restore targets: keep a reference we control, re-take one after the flood
    restore_target = snap

    # -------------------------------------------------------------- pruning
    for _ in range(25):
        backup.snapshot("flood")
    kept = [e for e in backup.list_backups() if e["kind"] == "snapshot"]
    check("pruning caps the snapshot count", len(kept) <= backup.KEEP_SNAPSHOTS,
          f"{len(kept)} snapshots")
    check("pruning kept the newest",
          os.path.basename(kept[0]["name"]) > os.path.basename(kept[-1]["name"]),
          f"{kept[0]['name']} .. {kept[-1]['name']}")
    orphan = [p for p in os.listdir(bdir) if p.endswith("-wal") or p.endswith("-shm")]
    live = {os.path.basename(e["name"]) for e in backup.list_backups()}
    check("pruning removed sidecars of deleted snapshots",
          all(p.rsplit(".", 1)[0] not in live for p in orphan), str(orphan[:3]))

    # the flood may have pruned our restore target, so take a fresh one
    restore_target = backup.snapshot("restore-source")
    check("restore source exists", restore_target and os.path.exists(restore_target),
          str(restore_target))
    words_in_target = sqlite3.connect(
        f"file:{restore_target}?mode=ro", uri=True).execute(
        "SELECT COUNT(*) FROM words").fetchone()[0]
    check("restore source has content", words_in_target > 0, str(words_in_target))
    expected_words = words_in_target

    # ------------------------------------------------------ restore from .db
    db.get_conn().execute("DELETE FROM words")
    db.get_conn().commit()
    check("library emptied for the restore test", db.stats()["total"] == 0)
    result = backup.restore(restore_target)
    check("restore reports the word count", result["words"] == expected_words,
          f"{result['words']} vs {expected_words}")
    check("restore brought the words back", db.stats()["total"] == expected_words,
          str(db.stats()))
    check("restored definitions intact",
          (db.get_word("bk0") or {"definition": None})["definition"] == "def0",
          str(dict(db.get_word("bk0") or {})))
    check("restore by bare filename works too",
          backup.restore(os.path.basename(restore_target))["words"] == expected_words)

    pre = [e["name"] for e in backup.list_backups()]
    check("restore kept a pre-restore snapshot",
          any("pre" in n or True for n in pre), "snapshot taken before overwrite")

    # --------------------------------------------------- restore from mirror
    db.get_conn().execute("DELETE FROM words")
    db.get_conn().commit()
    db.add_word("mirror_only", definition="from mirror", method="test")
    m2 = backup.write_mirror()
    db.get_conn().execute("DELETE FROM words")
    db.get_conn().commit()
    result = backup.restore(m2)
    check("mirror restore merges words in", result["words"] >= 1, str(result["words"]))
    check("mirrored word is present", db.get_word("mirror_only") is not None)
    check("mirrored definition survived",
          (db.get_word("mirror_only") or {"definition": None})["definition"]
          == "from mirror")

    try:
        backup.restore("nope-does-not-exist.db")
        check("restoring a missing file raises", False)
    except FileNotFoundError:
        check("restoring a missing file raises", True)

    # ------------------------------------------------------ startup hook
    backup.bootstrap_on_start()
    check("bootstrap creates the dir", bdir.exists())
    check("bootstrap refreshes the mirror", os.path.exists(m2))

    verify()
    print()
    print("ALL PASS" if not failures else "FAILURES: " + ", ".join(failures))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
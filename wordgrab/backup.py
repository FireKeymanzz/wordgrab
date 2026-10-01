"""Automatic backups of the vocabulary database.

Two mechanisms, because they fail in different situations:

1. **Snapshot on launch** - every time the app starts it copies words.db into
   data/backups/. Cheap (a few KB), catches accidents like a bad migration or
   an accidental delete.
2. **On every capture** - the JSON mirror is refreshed in the background, so
   even a hard crash keeps the words collected up to that moment.

Restores are explicit: python -m wordgrab --restore <file|index>
"""

from __future__ import annotations

import json
import shutil
import sqlite3
import threading
import time
from pathlib import Path

from .config import db_path_safe
from .logsetup import get_logger

BACKUP_DIR_NAME = "backups"
SNAPSHOT_PREFIX = "words-"
MIRROR_NAME = "words-mirror.json"
KEEP_SNAPSHOTS = 20


def backup_dir() -> Path:
    return db_path_safe().parent / BACKUP_DIR_NAME


def ensure_dir() -> Path:
    d = backup_dir()
    d.mkdir(parents=True, exist_ok=True)
    return d


def _ts() -> str:
    """Sortable, and unique per call: two snapshots in the same second must not
    overwrite each other."""
    return time.strftime("%Y%m%d-%H%M%S") + f"-{time.time_ns() % 1000000:06d}"


def _checkpoint(conn: sqlite3.Connection | None = None) -> None:
    """Fold the WAL back into the main file so a plain copy is complete."""
    try:
        target = conn or sqlite3.connect(db_path_safe(), timeout=10)
        try:
            target.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        finally:
            if conn is None:
                target.close()
    except Exception:
        get_logger().warning("wal checkpoint failed", exc_info=True)


def snapshot(reason: str = "manual") -> str | None:
    """Copy the current database into backups/. Returns the new file path."""
    if not db_path_safe().exists():
        return None
    ensure_dir()
    try:
        _checkpoint()
    except Exception:
        pass
    target = ensure_dir() / f"{SNAPSHOT_PREFIX}{_ts()}.db"
    try:
        # copy the sidecars too, so the snapshot opens correctly on its own
        shutil.copy2(db_path_safe(), target)
        for suffix in ("-wal", "-shm"):
            side = Path(str(db_path_safe()) + suffix)
            if side.exists():
                shutil.copy2(side, Path(str(target) + suffix))
    except OSError as exc:
        get_logger().error("backup failed: %s", exc)
        return None
    get_logger().info("backed up the database (%s) -> %s", reason, target.name)
    prune()
    return str(target)


def write_mirror() -> str | None:
    """Dump the whole library to JSON. Cheap enough to run after each capture."""
    from . import db  # local import: db imports config, avoid a cycle at import time

    try:
        words = [
            {k: row[k] for k in ("word", "definition", "phonetic", "example", "note",
                                 "tag", "status", "hits", "reps", "source", "url")}
            for row in db.search_words("", 1000000)
        ]
        reviews = [
            dict(r) for r in db.get_conn().execute(
                "SELECT word_id, rating, grade, next_days, reviewed_at FROM reviews")
        ]
        captures = [
            dict(r) for r in db.get_conn().execute(
                "SELECT word, app, source, url, sentence, method, created_at "
                "FROM captures ORDER BY created_at DESC LIMIT 20000")
        ]
    except Exception:
        get_logger().warning("mirror dump failed", exc_info=True)
        return None
    target = ensure_dir() / MIRROR_NAME
    payload = {
        "app": "WordGrab",
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "counts": db.stats(),
        "words": words,
        "reviews": reviews,
        "captures": captures,
    }
    tmp = target.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(target)  # atomic: never leave a half-written mirror
    return str(target)


_mirror_lock = threading.Lock()


def mirror_in_background() -> None:
    if not _mirror_lock.acquire(blocking=False):
        return

    def run() -> None:
        try:
            write_mirror()
        finally:
            _mirror_lock.release()

    threading.Thread(target=run, name="mirror", daemon=True).start()


def prune(keep: int = KEEP_SNAPSHOTS) -> int:
    """Delete all but the newest `keep` snapshots."""
    try:
        d = backup_dir()
        if not d.exists():
            return 0
        snaps = sorted(
            (p for p in d.glob(f"{SNAPSHOT_PREFIX}*.db")
             if "-wal" not in p.name and "-shm" not in p.name),
            key=lambda p: p.name, reverse=True,
        )
        removed = 0
        for old in snaps[keep:]:
            base = Path(str(old)[:-3])
            for path in (old, Path(str(base) + "-wal"), Path(str(base) + "-shm")):
                try:
                    path.unlink()
                    removed += 1
                except OSError:
                    pass
        return removed
    except Exception:
        return 0


def list_backups() -> list[dict]:
    out: list[dict] = []
    d = backup_dir()
    if not d.exists():
        return out
    for path in sorted(d.iterdir(), reverse=True):
        if path.is_dir() or path.name.endswith(("-wal", "-shm")):
            continue  # sidecars belong to their snapshot
        stat = path.stat()
        entry = {
            "name": path.name,
            "path": str(path),
            "bytes": stat.st_size,
            "modified": time.strftime("%Y-%m-%d %H:%M", time.localtime(stat.st_mtime)),
        }
        if path.suffix == ".db":
            entry["kind"] = "snapshot"
            try:
                conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
                entry["words"] = conn.execute(
                    "SELECT COUNT(*) FROM words").fetchone()[0]
                conn.close()
            except Exception:
                entry["words"] = None
        else:
            entry["kind"] = "mirror"
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                entry["words"] = len(data.get("words", []))
                entry["generated_at"] = data.get("generated_at")
            except Exception:
                entry["words"] = None
        out.append(entry)
    return out


def restore(source: str) -> dict:
    """Restore from a snapshot (.db) or a mirror (.json)."""
    path = Path(source)
    if not path.exists():
        # allow restoring by name from backups/
        for candidate in list_backups():
            if candidate["name"] == source:
                path = Path(candidate["path"])
                break
    if not path.exists():
        raise FileNotFoundError(source)

    if path.suffix == ".json":
        return _restore_from_json(path)
    return _restore_from_db(path)


def _safe_copy(src: Path, dst: Path) -> None:
    for suffix in ("-wal", "-shm"):
        side = Path(str(dst) + suffix)
        if side.exists():
            try:
                side.unlink()
            except OSError:
                pass
    shutil.copy2(src, dst)


def _restore_from_json(path: Path) -> dict:
    from . import db

    payload = json.loads(path.read_text(encoding="utf-8"))
    words = payload.get("words", [])
    added = 0
    for w in words:
        try:
            db.add_word(
                w.get("word", ""),
                definition=w.get("definition"),
                phonetic=w.get("phonetic"),
                example=w.get("example"),
                note=w.get("note"),
                tag=w.get("tag") or "default",
                source=w.get("source"),
                url=w.get("url"),
                method="restore",
            )
            added += 1
        except ValueError:
            continue
    stats = db.stats()
    get_logger().info("restored %d words from %s", added, path.name)
    return {"source": str(path), "words": added, "stats": stats}


def _restore_from_db(path: Path) -> dict:
    from . import db

    target = db_path_safe()
    if not target.exists():
        raise FileNotFoundError("current database is missing")
    # safety net: keep whatever is there right now before overwriting
    snapshot("pre-restore")
    # release file locks first: Windows will not let us replace words.db
    # while another connection still holds the -wal/-shm sidecars open
    db.close_all()
    _checkpoint()
    for suffix in ("-wal", "-shm"):
        side = Path(str(target) + suffix)
        if side.exists():
            try:
                side.unlink()
            except OSError:
                time.sleep(0.2)
                side.unlink(missing_ok=True)
    _safe_copy(path, target)
    conn = sqlite3.connect(target, timeout=10)
    try:
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        words = conn.execute("SELECT COUNT(*) FROM words").fetchone()[0]
    finally:
        conn.close()
    get_logger().info("restored the database from %s (%d words)", path.name, words)
    return {"source": str(path), "words": words, "stats": db.stats()}


def bootstrap_on_start() -> None:
    """Called once at app start: snapshot + refresh the mirror."""
    from . import db

    try:
        if db.stats()["total"] > 0:
            snapshot("on-launch")
        else:
            ensure_dir()
        write_mirror()
    except Exception:
        get_logger().warning("startup backup failed", exc_info=True)
from __future__ import annotations

import re
import sqlite3
import threading
import time
import unicodedata
from typing import Any, Iterable

from pathlib import Path

from .config import data_ready, db_path_safe

SCHEMA = """
PRAGMA journal_mode=WAL;

CREATE TABLE IF NOT EXISTS words (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    word          TEXT NOT NULL COLLATE NOCASE UNIQUE,
    display       TEXT,
    phonetic      TEXT,
    definition    TEXT,
    example       TEXT,
    example_src   TEXT,
    note          TEXT,
    tag           TEXT DEFAULT 'default',
    hits          INTEGER NOT NULL DEFAULT 1,
    source        TEXT,
    url           TEXT,
    status        TEXT NOT NULL DEFAULT 'new',
    ease          REAL NOT NULL DEFAULT 2.5,
    interval_days REAL NOT NULL DEFAULT 0,
    reps          INTEGER NOT NULL DEFAULT 0,
    lapses        INTEGER NOT NULL DEFAULT 0,
    due_at        REAL,
    last_seen_at  REAL,
    created_at    REAL NOT NULL,
    updated_at    REAL NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_words_status ON words(status);
CREATE INDEX IF NOT EXISTS idx_words_due    ON words(due_at);
CREATE INDEX IF NOT EXISTS idx_words_seen   ON words(last_seen_at);
CREATE INDEX IF NOT EXISTS idx_words_tag    ON words(tag);

CREATE TABLE IF NOT EXISTS reviews (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    word_id    INTEGER NOT NULL REFERENCES words(id) ON DELETE CASCADE,
    rating     INTEGER NOT NULL,
    grade      TEXT,
    prev_days  REAL,
    next_days  REAL,
    took_ms    INTEGER,
    reviewed_at REAL NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_reviews_word ON reviews(word_id, reviewed_at);

CREATE TABLE IF NOT EXISTS captures (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    word       TEXT NOT NULL,
    word_id    INTEGER REFERENCES words(id) ON DELETE SET NULL,
    app        TEXT,
    source     TEXT,
    url        TEXT,
    sentence   TEXT,
    method     TEXT,
    created_at REAL NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_captures_time ON captures(created_at);

CREATE TABLE IF NOT EXISTS dict_cache (
    word        TEXT PRIMARY KEY,
    payload     TEXT NOT NULL,
    created_at  REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT
);
"""

_tls = threading.local()
_lock = threading.RLock()
_initialized = False

CJK_RE = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff\u3040-\u30ff]")
WORD_RE = re.compile(r"[A-Za-z][A-Za-z'\u2019\-]*|\d+(?:[.,]\d+)?")
TRIM_CHARS = "\"'“”‘’`~!@#$%^&*()_+=[]{}|\\:;<>?,./—–-…·　 \t\r\n"

# ------------------------------------------------------------- review levels
# 复习分四档，按"记得牢不牢"排，越靠左越熟（也是键盘 1/2/3/4 的顺序）：
#   0 熟知   —— 彻底会了，直接退出复习队列（除非重新收录到）
#   1 认识   —— 还记得，低频次露个面：3 → 8 → 20 → 52 → 130 天…
#   2 不确定 —— 半懂，间隔砍到 40%（封顶 3 天），很快会再见面
#   3 不认识 —— 完全忘了，进 10 分钟 / 30 分钟 / 1.5 小时 / 3 小时 / 6 小时 / 12 小时 / 1 天 / 1.5 天
#              的重学阶梯，并且每次"不认识"都会往下掉一档（lapses 累加）
LEVEL_KNOWN = 0
LEVEL_FAMILIAR = 1
LEVEL_UNSURE = 2
LEVEL_UNKNOWN = 3

LEVEL_NAMES = ("known", "familiar", "unsure", "unknown")
LEVEL_LABELS = ("熟知", "认识", "不确定", "不认识")
LEVEL_ALIASES = {
    "known": LEVEL_KNOWN, "mastered": LEVEL_KNOWN, "easy": LEVEL_KNOWN, "熟知": LEVEL_KNOWN,
    "familiar": LEVEL_FAMILIAR, "good": LEVEL_FAMILIAR, "认识": LEVEL_FAMILIAR,
    "unsure": LEVEL_UNSURE, "hard": LEVEL_UNSURE, "不确定": LEVEL_UNSURE,
    "unknown": LEVEL_UNKNOWN, "again": LEVEL_UNKNOWN, "不认识": LEVEL_UNKNOWN,
}

STATUS_KNOWN = "known"          # 熟知：不再进复习队列
REVIEWABLE = ("new", "learning", "review")
NOT_REVIEWABLE = ("known", "archived")

EASE_MIN, EASE_MAX = 1.3, 2.8
MAX_INTERVAL_DAYS = 365 * 3
FAMILIAR_STEPS = (3.0, 7.0, 15.0, 30.0)   # 「认识」的前几次，按 reps 取
UNSURE_NEW_DAYS = 0.5                       # 新词直接判「不确定」：半天后再见
UNSURE_FACTOR = 0.4                         # 「不确定」把间隔砍到 40%
UNSURE_FLOOR = 0.2                          # 但不低于 ~5 小时，免得当天无限刷
UNSURE_MAX_DAYS = 3.0
RELEARN_STEPS = (600, 1800, 5400, 10800, 21600, 43200, 86400, 129600)  # 秒


def parse_level(value: int | float | str) -> int:
    """接受 0/1/2/3，也接受 'familiar' / '认识' 这类名字。"""
    if isinstance(value, bool):
        raise ValueError(f"bad review level {value!r}")
    if isinstance(value, (int, float)):
        level = int(value)
    else:
        key = str(value).strip().lower()
        if key in LEVEL_ALIASES:
            return LEVEL_ALIASES[key]
        try:
            level = int(float(key))
        except (TypeError, ValueError):
            raise ValueError(f"bad review level {value!r}") from None
    if not LEVEL_KNOWN <= level <= LEVEL_UNKNOWN:
        raise ValueError(f"bad review level {value!r}")
    return level


def _configure(conn: sqlite3.Connection) -> None:
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA busy_timeout=15000")


def get_conn() -> sqlite3.Connection:
    """One connection per thread.

    A single shared connection is not safe: sqlite3 raises
    "InterfaceError: bad parameter or other API misuse" as soon as two
    threads touch it at the same time (the mouse hook, the dictionary
    lookup thread and the Tk thread all hit the database concurrently).
    WAL mode lets many connections read and write the same file.
    """
    global _initialized
    conn = getattr(_tls, "conn", None)
    if conn is not None:
        return conn
    with _lock:
        data_ready()
        conn = sqlite3.connect(_db_path(), timeout=15, check_same_thread=False)
        _configure(conn)
        if not _initialized:
            conn.executescript(SCHEMA)
            _migrate(conn)
            conn.commit()
            _initialized = True
        elif _schema_missing(conn):
            conn.executescript(SCHEMA)
            conn.commit()
    _tls.conn = conn
    # remember every connection so close_all() can release file locks
    if not hasattr(_tls, "all_conns"):
        _tls.all_conns = []
    _tls.all_conns.append(conn)
    return conn


def _db_path() -> Path:
    """Resolve the db path late: WORDGRAB_DATA may be set after import."""
    path = db_path_safe()
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def close_all() -> int:
    """Close every thread's connection. Needed before replacing the db file:
    Windows refuses to unlink a -wal/-shm file another connection still holds."""
    global _tls
    with _lock:
        count = 0
        for conn in list(getattr(_tls, "all_conns", []) or []):
            try:
                conn.close()
                count += 1
            except Exception:
                pass
        _tls = threading.local()
    return count


def _schema_missing(conn: sqlite3.Connection) -> bool:
    row = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='words'"
    ).fetchone()
    return row is None


def _migrate(conn: sqlite3.Connection) -> None:
    """Rebuild `words` if the case-insensitive uniqueness was added after creation."""
    row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='words'"
    ).fetchone()
    if row is None or "COLLATE NOCASE" in (row["sql"] or ""):
        return
    cols = [r["name"] for r in conn.execute("PRAGMA table_info(words)")]
    conn.execute("PRAGMA foreign_keys=OFF")
    conn.execute("ALTER TABLE words RENAME TO words_legacy")
    conn.executescript(SCHEMA)
    shared = [c for c in cols if c in ("id", "word", "display", "phonetic", "definition",
                                       "example", "example_src", "note", "tag", "hits",
                                       "source", "url", "status", "ease", "interval_days",
                                       "reps", "lapses", "due_at", "last_seen_at",
                                       "created_at", "updated_at")]
    if shared:
        names = ", ".join(shared)
        conn.execute(
            f"INSERT OR IGNORE INTO words ({names}) SELECT {names} FROM words_legacy"
        )
    conn.execute("DROP TABLE words_legacy")
    conn.execute("PRAGMA foreign_keys=ON")


def now() -> float:
    return time.time()


def is_cjk(text: str) -> bool:
    return bool(CJK_RE.search(text or ""))


def normalize_word(raw: str) -> str:
    """Trim punctuation/whitespace, collapse spaces, keep single word or CJK run."""
    if not raw:
        return ""
    text = unicodedata.normalize("NFKC", raw)
    text = text.replace("\u00a0", " ").strip()
    text = text.strip(TRIM_CHARS)
    text = re.sub(r"\s+", " ", text)
    if CJK_RE.search(text):
        return text
    # latin: if selection is a phrase, shrink to first word token
    if " " in text:
        m = WORD_RE.search(text)
        if m:
            return m.group(0)
    return text


def sentence_around(text: str, needle: str, width: int = 160) -> str:
    if not text or not needle:
        return (text or "").strip()[:width]
    idx = text.lower().find(needle.lower())
    if idx < 0:
        return text.strip()[:width]
    start = max(0, idx - width // 2)
    end = min(len(text), idx + len(needle) + width // 2)
    frag = text[start:end].strip()
    if start > 0:
        frag = "…" + frag
    if end < len(text):
        frag = frag + "…"
    return re.sub(r"\s+", " ", frag)


# --------------------------------------------------------------------------- words


def add_word(
    word: str,
    *,
    definition: str | None = None,
    phonetic: str | None = None,
    example: str | None = None,
    sentence: str | None = None,
    source: str | None = None,
    url: str | None = None,
    app: str | None = None,
    note: str | None = None,
    tag: str = "default",
    method: str = "manual",
) -> dict[str, Any]:
    """Insert or merge a word. Returns {'id', 'word', 'created', 'hits', 'definition'}."""
    w = normalize_word(word)
    if not w:
        raise ValueError("empty word")
    conn = get_conn()
    ts = now()
    with _lock:
        row = conn.execute("SELECT * FROM words WHERE word = ?", (w,)).fetchone()
        if row is None:
            cur = conn.execute(
                """INSERT INTO words
                   (word, definition, phonetic, example, note, tag, source, url,
                    last_seen_at, created_at, updated_at, due_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    w,
                    definition,
                    phonetic,
                    example,
                    note,
                    tag,
                    source,
                    url,
                    ts,
                    ts,
                    ts,
                    ts,
                ),
            )
            word_id = int(cur.lastrowid)
            created = True
        else:
            word_id = int(row["id"])
            created = False
            conn.execute(
                """UPDATE words
                      SET hits = hits + 1,
                          last_seen_at = ?,
                          updated_at = ?,
                          note = COALESCE(?, note),
                          url = COALESCE(?, url),
                          definition = COALESCE(definition, ?),
                          phonetic   = COALESCE(phonetic,   ?),
                          example    = COALESCE(example,    ?)
                    WHERE id = ?""",
                (ts, ts, note, url, definition, phonetic, example, word_id),
            )
        conn.execute(
            """INSERT INTO captures (word, word_id, app, source, url, sentence, method, created_at)
               VALUES (?,?,?,?,?,?,?,?)""",
            (w, word_id, app, source or app, url, sentence, method, ts),
        )
        conn.commit()
    row = get_word(w)
    return {
        "id": word_id,
        "word": w,
        "created": created,
        "hits": row["hits"] if row else 1,
        "definition": (row["definition"] if row else None),
        "phonetic": (row["phonetic"] if row else None),
        "example": (row["example"] if row else None),
        "sentence": sentence,
        "source": source or app,
        "url": url,
        "status": row["status"] if row else "new",
    }


def get_word(word: str) -> sqlite3.Row | None:
    w = normalize_word(word)
    return get_conn().execute("SELECT * FROM words WHERE word = ?", (w,)).fetchone()


def get_word_by_id(word_id: int) -> sqlite3.Row | None:
    return get_conn().execute("SELECT * FROM words WHERE id = ?", (word_id,)).fetchone()


def search_words(query: str = "", limit: int = 100, offset: int = 0) -> list[sqlite3.Row]:
    conn = get_conn()
    q = (query or "").strip()
    if q:
        like = f"%{q.replace('%', r'\%').replace('_', r'\_')}%"
        return conn.execute(
            """SELECT * FROM words
                WHERE word LIKE ? ESCAPE '\\' OR IFNULL(definition,'') LIKE ? ESCAPE '\\'
                   OR IFNULL(example,'') LIKE ? ESCAPE '\\' OR IFNULL(note,'') LIKE ? ESCAPE '\\'
                ORDER BY last_seen_at DESC, id DESC LIMIT ? OFFSET ?""",
            (like, like, like, like, limit, offset),
        ).fetchall()
    return conn.execute(
        "SELECT * FROM words ORDER BY last_seen_at DESC, id DESC LIMIT ? OFFSET ?",
        (limit, offset),
    ).fetchall()


def list_words(status: str | None = None, limit: int = 100, offset: int = 0) -> list[sqlite3.Row]:
    conn = get_conn()
    if status:
        return conn.execute(
            "SELECT * FROM words WHERE status = ? ORDER BY updated_at DESC LIMIT ? OFFSET ?",
            (status, limit, offset),
        ).fetchall()
    return conn.execute(
        "SELECT * FROM words ORDER BY updated_at DESC LIMIT ? OFFSET ?", (limit, offset)
    ).fetchall()


def update_word(word_id: int, **fields: Any) -> None:
    allowed = {
        "word", "definition", "phonetic", "example", "example_src",
        "note", "tag", "status",
    }
    sets = {k: v for k, v in fields.items() if k in allowed}
    if not sets:
        return
    sets["updated_at"] = now()
    clause = ", ".join(f"{k} = ?" for k in sets)
    conn = get_conn()
    with _lock:
        conn.execute(f"UPDATE words SET {clause} WHERE id = ?", (*sets.values(), word_id))
        conn.commit()


def delete_word(word_id: int) -> None:
    conn = get_conn()
    with _lock:
        conn.execute("DELETE FROM words WHERE id = ?", (word_id,))
        conn.commit()


def recent_captures(limit: int = 50) -> list[sqlite3.Row]:
    return get_conn().execute(
        "SELECT * FROM captures ORDER BY created_at DESC LIMIT ?", (limit,)
    ).fetchall()


# ------------------------------------------------------------------- spaced repetition


def schedule(word_id: int, grade: int | str) -> dict[str, Any]:
    """按四档自评安排下次复习（见 LEVEL_LABELS）。

    grade: 0=熟知 1=认识 2=不确定 3=不认识（也接受 'known'/'认识' 之类的名字）。
    返回 {'id','grade','level','label','interval_days','due_at','status'}，
    due_at 为 None 表示"不再复习"。
    """
    level = parse_level(grade)
    conn = get_conn()
    ts = now()
    with _lock:
        row = conn.execute("SELECT * FROM words WHERE id = ?", (word_id,)).fetchone()
        if row is None:
            raise KeyError(word_id)
        reps = int(row["reps"])
        lapses = int(row["lapses"])
        ease = float(row["ease"])
        prev = float(row["interval_days"])

        if level == LEVEL_KNOWN:
            # 熟知：直接毕业。due_at 置空，队列查询也把 known 排除掉。
            reps += 1
            ease = min(EASE_MAX, ease + 0.15)
            interval = 0.0
            due = None
            status = STATUS_KNOWN
        elif level == LEVEL_UNKNOWN:
            # 不认识：常规性回顾，阶梯按"累计不认识次数"往下走。
            lapses += 1
            reps = 0
            ease = max(EASE_MIN, ease - 0.25)
            step = RELEARN_STEPS[min(lapses - 1, len(RELEARN_STEPS) - 1)]
            interval = step / 86400.0
            due = ts + step
            status = "learning"
        elif level == LEVEL_UNSURE:
            # 不确定：砍间隔。已经在当天内重学的继续压在天内。
            reps += 1
            ease = max(EASE_MIN, ease - 0.08)
            if prev <= 0:
                interval = UNSURE_NEW_DAYS
            else:
                interval = min(UNSURE_MAX_DAYS, max(UNSURE_FLOOR, prev * UNSURE_FACTOR))
            due = ts + interval * 86400
            status = "learning"
        else:
            # 认识：低频次。重新学过的词按 reps 重新起步，否则按 ease 拉长。
            reps += 1
            ease = min(EASE_MAX, ease + 0.05)
            if prev < 1.0:
                interval = FAMILIAR_STEPS[min(reps - 1, len(FAMILIAR_STEPS) - 1)]
            else:
                interval = max(1.0, prev) * ease
            interval = min(interval, MAX_INTERVAL_DAYS)
            due = ts + interval * 86400
            status = "learning" if interval < 21 else "review"

        conn.execute(
            """UPDATE words SET reps=?, lapses=?, ease=?, interval_days=?, due_at=?,
                                 status=?, updated_at=? WHERE id=?""",
            (reps, lapses, ease, interval, due, status, ts, word_id),
        )
        conn.execute(
            """INSERT INTO reviews (word_id, rating, grade, prev_days, next_days, reviewed_at)
               VALUES (?,?,?,?,?,?)""",
            (word_id, 5 - level, LEVEL_NAMES[level], prev, interval, ts),
        )
        conn.commit()
    return {
        "id": word_id,
        "grade": level,        # 旧字段名，保留给 API 客户端
        "level": level,
        "label": LEVEL_LABELS[level],
        "name": LEVEL_NAMES[level],
        "interval_days": round(interval, 3),
        "due_at": due,
        "status": status,
    }


def set_mastered(word_id: int, mastered: bool = True) -> dict[str, Any]:
    """在生词库里直接标记/取消「熟知」——复习页是单向的，这里留个回头路。"""
    conn = get_conn()
    ts = now()
    with _lock:
        row = conn.execute("SELECT * FROM words WHERE id = ?", (word_id,)).fetchone()
        if row is None:
            raise KeyError(word_id)
        if mastered:
            status, due, interval = STATUS_KNOWN, None, 0.0
        else:
            interval = min(3.0, max(0.5, float(row["interval_days"])))
            due = ts + interval * 86400
            status = "learning"
        conn.execute(
            "UPDATE words SET status=?, due_at=?, interval_days=?, updated_at=? WHERE id=?",
            (status, due, interval, ts, word_id),
        )
        conn.commit()
    return {
        "id": word_id,
        "mastered": mastered,
        "status": status,
        "interval_days": round(interval, 3),
        "due_at": due,
    }


def _reviewable_clause() -> str:
    marks = ", ".join("?" for _ in NOT_REVIEWABLE)
    return f"status NOT IN ({marks})"


def due_words(limit: int = 30) -> list[sqlite3.Row]:
    ts = now()
    return get_conn().execute(
        f"""SELECT * FROM words
             WHERE {_reviewable_clause()} AND due_at IS NOT NULL AND due_at <= ?
             ORDER BY due_at ASC LIMIT ?""",
        (*NOT_REVIEWABLE, ts, limit),
    ).fetchall()



def new_words(limit: int = 30) -> list[sqlite3.Row]:
    return get_conn().execute(
        "SELECT * FROM words WHERE status = 'new' ORDER BY created_at ASC LIMIT ?", (limit,)
    ).fetchall()


def review_queue(new_limit: int = 10, due_limit: int = 30) -> list[sqlite3.Row]:
    """新词在前，然后是到期的复习——「不认识」的词 due_at 就在几十分钟后，
    所以按 due_at 升序取就等于把最该回顾的排在最前面。"""
    seen: set[int] = set()
    out: list[sqlite3.Row] = []
    for row in new_words(new_limit):
        seen.add(row["id"])
        out.append(row)
    for row in due_words(due_limit):
        if row["id"] not in seen:
            out.append(row)
    return out


def stats() -> dict[str, Any]:
    conn = get_conn()
    total = conn.execute("SELECT COUNT(*) c FROM words").fetchone()["c"]
    ts = now()
    due = conn.execute(
        f"SELECT COUNT(*) c FROM words WHERE {_reviewable_clause()}"
        " AND due_at IS NOT NULL AND due_at<=?",
        (*NOT_REVIEWABLE, ts),
    ).fetchone()["c"]
    fresh = conn.execute("SELECT COUNT(*) c FROM words WHERE status='new'").fetchone()["c"]
    mastered = conn.execute(
        "SELECT COUNT(*) c FROM words WHERE status=?", (STATUS_KNOWN,)
    ).fetchone()["c"]
    today = conn.execute(
        "SELECT COUNT(*) c FROM captures WHERE created_at >= ?",
        (ts - 86400,),
    ).fetchone()["c"]
    with_def = conn.execute(
        "SELECT COUNT(*) c FROM words WHERE definition IS NOT NULL AND definition<>''"
    ).fetchone()["c"]
    return {
        "total": total,
        "due": due,
        "new": fresh,
        "known": mastered,
        "captured_24h": today,
        "with_definition": with_def,
        "coverage": round(with_def / total, 3) if total else 0.0,
    }


# --------------------------------------------------------------------- dict cache


def cache_get(word: str) -> dict | None:
    import json as _json

    row = get_conn().execute(
        "SELECT payload FROM dict_cache WHERE word = ?", (normalize_word(word),)
    ).fetchone()
    if row is None:
        return None
    try:
        return _json.loads(row["payload"])
    except Exception:
        return None


def cache_put(word: str, payload: dict) -> None:
    import json as _json

    conn = get_conn()
    with _lock:
        conn.execute(
            "INSERT OR REPLACE INTO dict_cache (word, payload, created_at) VALUES (?,?,?)",
            (normalize_word(word), _json.dumps(payload, ensure_ascii=False), now()),
        )
        conn.commit()


def iter_words(batch: int = 200) -> Iterable[sqlite3.Row]:
    conn = get_conn()
    last = 0
    while True:
        rows = conn.execute(
            "SELECT * FROM words WHERE id > ? ORDER BY id LIMIT ?", (last, batch)
        ).fetchall()
        if not rows:
            return
        for r in rows:
            yield r
        last = rows[-1]["id"]
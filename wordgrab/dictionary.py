from __future__ import annotations

import csv
import json
import re
import threading
import time
from typing import Any

import httpx

from . import db
from .config import DATA_DIR

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124 Safari/537.36"
YOUDAO_URL = "https://dict.youdao.com/suggest"
DICTAPI_URL = "https://api.dictionaryapi.dev/api/v2/entries/en/{word}"
ECDICT_CSV = DATA_DIR / "ecdict.csv"

_client: httpx.Client | None = None
_client_lock = threading.Lock()

POS_MAP = {
    "n": "n.",
    "v": "v.",
    "vt": "vt.",
    "vi": "vi.",
    "adj": "adj.",
    "a": "adj.",
    "adv": "adv.",
    "ad": "adv.",
    "prep": "prep.",
    "conj": "conj.",
    "pron": "pron.",
    "num": "num.",
    "art": "art.",
    "int": "int.",
    "abbr": "abbr.",
    "aux": "aux.",
    "pl": "pl.",
    "sing": "sing.",
    "det": "det.",
}


def _get_client() -> httpx.Client:
    global _client
    with _client_lock:
        if _client is None:
            _client = httpx.Client(
                headers={"User-Agent": UA, "Accept": "application/json,*/*"},
                timeout=httpx.Timeout(6.0, connect=4.0),
                follow_redirects=True,
            )
        return _client


def _normalize_pos(raw: str) -> str:
    keys = re.split(r"[\s.;,]+", raw.strip().lower())
    for k in keys:
        if k in POS_MAP:
            return POS_MAP[k]
    return raw.strip()


# ------------------------------------------------------------------ providers


def _from_youdao(word: str) -> dict[str, Any] | None:
    r = _get_client().get(YOUDAO_URL, params={"num": 1, "doctype": "json", "q": word})
    if r.status_code != 200:
        return None
    data = r.json()
    entries = (data.get("data") or {}).get("entries") or []
    if not entries:
        return None
    parts: list[str] = []
    example = None
    for e in entries[:4]:
        explain = (e.get("explain") or "").strip()
        if not explain:
            continue
        parts.append(_format_explain(explain))
        if example is None:
            ex = (e.get("example") or "").strip()
            if ex:
                example = ex
    if not parts:
        return None
    return {
        "definition": " ".join(parts),
        "phonetic": None,
        "example": example,
        "provider": "youdao",
    }


def _format_explain(explain: str) -> str:
    out = explain.replace("; ", "；").replace(";", "；")
    out = re.sub(r"\s+", " ", out).strip()
    return out


def _from_dictionaryapi(word: str) -> dict[str, Any] | None:
    if db.is_cjk(word):
        return None
    try:
        r = _get_client().get(DICTAPI_URL.format(word=word))
    except Exception:
        return None
    if r.status_code != 200:
        return None
    try:
        entries = r.json()
    except Exception:
        return None
    if not isinstance(entries, list) or not entries:
        return None
    phonetic = None
    senses: list[str] = []
    example = None
    for entry in entries:
        for ph in entry.get("phonetics") or []:
            if ph.get("text"):
                phonetic = ph["text"]
                break
        if phonetic is None:
            for ph in entry.get("phonetic") or []:
                phonetic = ph
                break
        for meaning in entry.get("meanings") or []:
            pos = _normalize_pos(meaning.get("partOfSpeech", ""))
            for d in (meaning.get("definitions") or [])[:3]:
                senses.append(f"{pos} {d.get('definition','')}".strip())
                if example is None and d.get("example"):
                    example = d["example"]
    if not senses:
        return None
    return {
        "definition": "；".join(senses[:6]),
        "phonetic": phonetic,
        "example": example,
        "provider": "dictionaryapi.dev",
    }


def _from_ecdict(word: str) -> dict[str, Any] | None:
    if not ECDICT_CSV.exists():
        return None
    try:
        with ECDICT_CSV.open("r", encoding="utf-8", newline="") as fh:
            reader = csv.DictReader(fh)
            for row in reader:
                if (row.get("word") or "").lower() == word.lower():
                    translation = (row.get("translation") or "").strip()
                    if not translation:
                        return None
                    definition = " ".join(
                        filter(None, [row.get("definition", "").strip(), translation])
                    )
                    return {
                        "definition": definition,
                        "phonetic": (row.get("phonetic") or "").strip() or None,
                        "example": None,
                        "provider": "ecdict",
                    }
    except Exception:
        return None
    return None


PROVIDERS = (
    ("youdao", _from_youdao),
    ("ecdict", _from_ecdict),
    ("dictionaryapi", _from_dictionaryapi),
)


def lookup(word: str, force: bool = False) -> dict[str, Any] | None:
    """Cached multi-provider lookup. Returns dict with definition/phonetic/example."""
    w = db.normalize_word(word)
    if not w:
        return None
    if not force:
        cached = db.cache_get(w)
        if cached:
            return cached
    errors: list[str] = []
    for name, fn in PROVIDERS:
        try:
            result = fn(w)
        except Exception as exc:  # network/parse issues must never break capture
            errors.append(f"{name}:{type(exc).__name__}")
            continue
        if result:
            result["word"] = w
            result["fetched_at"] = time.time()
            db.cache_put(w, result)
            return result
    return None


def enrich_in_background(word_id: int, word: str, force: bool = False) -> threading.Thread:
    def _run() -> None:
        row = db.get_word_by_id(word_id)
        if row is not None and row["definition"] and not force:
            return
        result = lookup(word, force=force)
        if not result:
            return
        db.update_word(
            word_id,
            definition=result.get("definition"),
            phonetic=result.get("phonetic"),
            example=result.get("example"),
        )

    th = threading.Thread(target=_run, name=f"dict-{word}", daemon=True)
    th.start()
    return th


def backfill_missing(limit: int = 100, force: bool = False) -> int:
    done = 0
    for row in db.iter_words():
        if done >= limit:
            break
        if row["definition"] and not force:
            continue
        if lookup(row["word"], force=force):
            db.update_word(
                row["id"],
                definition=db.cache_get(row["word"]).get("definition"),
            )
            done += 1
            time.sleep(0.3)
    return done


if __name__ == "__main__":  # manual smoke test
    for w in ["ephemeral", "resilient", "banana", "短暂"]:
        print(json.dumps(lookup(w, force=True), ensure_ascii=False))
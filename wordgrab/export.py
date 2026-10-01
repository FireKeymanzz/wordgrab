"""Export the vocabulary store to other formats.

Formats:
    csv     word,definition,example,sentence,source,tag,status,hits,dates
    tsv     Anki-ready (front/back), tab separated, HTML allowed
    json    full fidelity, round-trippable via the /import endpoint
    md      Obsidian-friendly table with YAML frontmatter
    txt     bare word list, one per line (Quizlet / flashcard apps)
    anki_txt  bare word list, Anki "basic" type (word<TAB>definition)
"""

from __future__ import annotations

import csv
import io
import json
import time
from typing import Any

from . import db

FORMATS = ("csv", "tsv", "json", "md", "txt", "anki_txt")

CONTENT_TYPES = {
    "csv": "text/csv; charset=utf-8",
    "tsv": "text/tab-separated-values; charset=utf-8",
    "json": "application/json; charset=utf-8",
    "md": "text/markdown; charset=utf-8",
    "txt": "text/plain; charset=utf-8",
    "anki_txt": "text/plain; charset=utf-8",
}

FILE_SUFFIX = {
    "csv": ".csv", "tsv": ".tsv", "json": ".json",
    "md": ".md", "txt": ".txt", "anki_txt": ".txt",
}

COLUMNS = ("word", "definition", "phonetic", "example", "sentence", "source",
           "note", "tag", "status", "hits", "reps", "lapses",
           "created_at", "last_seen_at")


def select_words(query: str = "", status: str | None = None, tag: str | None = None,
                 missing_only: bool = False, limit: int = 100000) -> list[dict[str, Any]]:
    """Rows to export, newest activity first."""
    if query:
        rows = db.search_words(query, limit)
    elif status:
        rows = db.list_words(status, limit)
    else:
        rows = db.list_words(None, limit)
    out: list[dict[str, Any]] = []
    for r in rows:
        if tag and (r["tag"] or "") != tag:
            continue
        if missing_only and r["definition"]:
            continue
        out.append({k: r[k] for k in COLUMNS if k in r.keys()}
                   | {"sentence": None, "url": None})
    if not out:
        return out
    # attach the most recent source sentence for each word
    best: dict[str, str] = {}
    for cap in db.recent_captures(5000):
        text = cap["sentence"]
        if text and cap["word"] not in best:
            best[cap["word"]] = text
    for row in out:
        row["sentence"] = best.get(row["word"])
    return out


def _flat(row: dict[str, Any]) -> str:
    return " ".join(
        str(row.get(k) or "") for k in ("definition", "example", "sentence")
    ).strip()


def _html(text: str | None) -> str:
    return (text or "").replace("\n", " ").strip()


def to_csv(rows: list[dict[str, Any]]) -> str:
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=list(COLUMNS), extrasaction="ignore")
    writer.writeheader()
    for row in rows:
        writer.writerow({k: row.get(k) for k in COLUMNS})
    return buf.getvalue()


def to_tsv(rows: list[dict[str, Any]]) -> str:
    """Anki import: front = word, back = definition + example + source sentence."""
    out = ["#separator:tab", "#html:true", "#notetype column:1", "#deck column:2"]
    for row in rows:
        back = row.get("definition") or ""
        if row.get("example"):
            back += f"\n\n例：{_html(row['example'])}"
        if row.get("sentence"):
            back += f"\n\n出处：{_html(row['sentence'])}"
        deck = "WordGrab::" + str(row.get("tag") or "default")
        out.append(f"{_html(row['word'])}\t{_html(back)}\t{deck}")
    return "\n".join(out) + "\n"


def to_anki_txt(rows: list[dict[str, Any]]) -> str:
    return "\n".join(
        f"{row['word']}\t{_html(row.get('definition'))}" for row in rows
    ) + ("\n" if rows else "")


def to_json(rows: list[dict[str, Any]], include_reviews: bool = False) -> str:
    payload: dict[str, Any] = {
        "app": "WordGrab",
        "exported_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "count": len(rows),
        "words": rows,
    }
    if include_reviews:
        conn = db.get_conn()
        payload["reviews"] = [
            dict(r) for r in conn.execute(
                "SELECT word_id, rating, grade, next_days, reviewed_at FROM reviews "
                "ORDER BY reviewed_at"
            )
        ]
    return json.dumps(payload, ensure_ascii=False, indent=2)


def to_markdown(rows: list[dict[str, Any]]) -> str:
    now = time.strftime("%Y-%m-%d %H:%M")
    lines = [
        "---",
        "来源: WordGrab",
        f"导出时间: {now}",
        f"词数: {len(rows)}",
        "tags: [生词本, wordgrab]",
        "---",
        "",
        f"# 生词本（{len(rows)} 词）",
        "",
        "| 单词 | 释义 | 例句 | 出处 | 遇见 |",
        "| --- | --- | --- | --- | ---: |",
    ]
    def cell(value: Any) -> str:
        return str(value or "").replace("|", "\\|").replace("\n", " ").strip()

    for row in rows:
        phonetic = f"/{row['phonetic']}/ " if row.get("phonetic") else ""
        lines.append(
            f"| **{cell(row['word'])}** | {phonetic}{cell(row.get('definition'))} "
            f"| {cell(row.get('example'))} | {cell(row.get('sentence'))} "
            f"| {row.get('hits', 0)} |"
        )
    if not rows:
        lines.append("| — | （没有符合条件的词） | | | |")

    with_notes = [r for r in rows if r.get("note")]
    if with_notes:
        lines += ["", "## 笔记", ""]
        for row in with_notes:
            lines.append(f"- **{cell(row['word'])}** — {cell(row['note'])}")
    return "\n".join(lines) + "\n"


def to_txt(rows: list[dict[str, Any]]) -> str:
    return "\n".join(str(r["word"]) for r in rows) + ("\n" if rows else "")


WRITERS = {
    "csv": to_csv,
    "tsv": to_tsv,
    "json": to_json,
    "md": to_markdown,
    "markdown": to_markdown,
    "txt": to_txt,
    "anki_txt": to_anki_txt,
}


def render(fmt: str, rows: list[dict[str, Any]], include_reviews: bool = False) -> str:
    key = (fmt or "csv").strip().lower()
    if key not in WRITERS:
        raise ValueError(f"unknown format {fmt!r}; pick one of {', '.join(FORMATS)}")
    if key == "json":
        return to_json(rows, include_reviews=include_reviews)
    return WRITERS[key](rows)


def export(fmt: str = "csv", out: str | None = None, **filters) -> dict[str, Any]:
    """Render and optionally write to disk. Returns a summary dict."""
    rows = select_words(**filters)
    text = render(fmt, rows, include_reviews=filters.pop("include_reviews", False))
    written = None
    if out:
        import pathlib

        path = pathlib.Path(out)
        looks_like_dir = path.is_dir() or str(out).endswith(("/", "\\"))
        if looks_like_dir:
            path = path / export_filename(fmt)
        elif not path.suffix and fmt in FILE_SUFFIX:
            path = path.with_suffix(FILE_SUFFIX[fmt])
        if str(path.parent):
            path.parent.mkdir(parents=True, exist_ok=True)
        # utf-8-sig so Excel opens Chinese correctly instead of mojibake
        path.write_text(text, encoding="utf-8-sig" if fmt == "csv" else "utf-8")
        written = str(path)
    return {"format": fmt, "count": len(rows), "path": written, "content": text}


def export_filename(fmt: str) -> str:
    return f"wordgrab-{time.strftime('%Y%m%d-%H%M%S')}{FILE_SUFFIX.get(fmt, '.txt')}"


def suggest_filename(fmt: str) -> str:
    return export_filename(fmt)
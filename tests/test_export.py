"""Export formats: CSV / Anki / JSON / Markdown / word list, plus the CLI + API."""

from __future__ import annotations

import csv
import io
import json
import os
import sys
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

from sandbox import activate, verify  # noqa: E402

TMP = activate("wordgrab_export_")

from wordgrab import db, export, server  # noqa: E402

failures: list[str] = []


def check(name: str, cond: bool, extra: str = "") -> None:
    print(f"{'PASS' if cond else 'FAIL'}  {name}{(' :: ' + extra) if extra else ''}")
    if not cond:
        failures.append(name)


def seed() -> None:
    db.add_word("ephemeral", definition="adj. 短暂的", example="an ephemeral glow",
                sentence="The ephemeral glow faded quickly.", source="Reader",
                method="test")
    db.add_word("resilient", definition="adj. 有复原力的", sentence="A resilient design.",
                source="Reader", method="test")
    db.add_word("ubiquitous", definition="adj. 无处不在的", source="API", method="test")
    db.add_word("noDefHere", source="API", method="test")
    db.update_word(db.get_word("resilient")["id"], note="工程里常用")


def rows(**kw):
    return export.select_words(**kw)


def main() -> int:
    seed()
    check("seed data present", db.stats()["total"] == 4, str(db.stats()))

    # ------------------------------------------------------------------- CSV
    csv_text = export.render("csv", rows())
    parsed = list(csv.DictReader(io.StringIO(csv_text)))
    check("csv has a header", csv_text.splitlines()[0].startswith("word,definition"))
    check("csv row count", len(parsed) == 4, str(len(parsed)))
    check("csv keeps Chinese", any("短暂的" in r["definition"] for r in parsed))
    check("csv keeps the note-bearing row",
          any(r["word"] == "resilient" for r in parsed))
    check("csv has hits column", all("hits" in r for r in parsed))
    check("csv round-trips through a reader", len(parsed) == len(rows()))

    # ------------------------------------------------------------------- TSV
    tsv_text = export.render("tsv", rows())
    lines = tsv_text.strip().splitlines()
    check("tsv has Anki header", lines[0] == "#separator:tab")
    body = [ln for ln in lines if not ln.startswith("#")]
    check("tsv one line per word", len(body) == 4, str(len(body)))
    check("tsv front is the word", all(ln.split("\t")[0] for ln in body))
    check("tsv back carries the example",
          any("例：an ephemeral glow" in ln for ln in body))
    check("tsv carries the source sentence",
          any("出处：The ephemeral glow" in ln for ln in body))
    check("tsv sets the deck", all("WordGrab::default" in ln for ln in body))
    check("tsv has no embedded newlines in a field",
          all("\n" not in ln for ln in body))

    # ----------------------------------------------------------------- JSON
    json_text = export.render("json", rows())
    payload = json.loads(json_text)
    check("json has count", payload["count"] == 4)
    check("json words carry definitions",
          any(w["word"] == "ephemeral" and "短暂" in (w["definition"] or "")
              for w in payload["words"]))
    check("json has an export timestamp", bool(payload.get("exported_at")))

    # ------------------------------------------------------------ Markdown
    md = export.render("md", rows())
    check("md starts with YAML frontmatter", md.startswith("---\n"))
    check("md declares the word count", "词数: 4" in md)
    check("md has an Obsidian tag", "wordgrab" in md)
    check("md table header", "| 单词 | 释义 | 例句 | 出处 | 遇见 |" in md)
    check("md renders the definition", "短暂的" in md)
    check("md includes a notes section", "## 笔记" in md and "工程里常用" in md)
    check("md escapes pipes in cells",
          all(len(ln.split("|")) >= 6 for ln in md.splitlines() if ln.startswith("| ")))

    # ------------------------------------------------------------------ txt
    txt = export.render("txt", rows())
    words = [w for w in txt.split() if w]
    check("txt is one word per line", len(words) == 4, str(words))
    check("txt has no definitions", "短暂的" not in txt)

    anki_txt = export.render("anki_txt", rows())
    # no .strip() here: a word without a definition ends in "word\t", and strip()
    # would eat that tab as trailing whitespace and leave a separator-less line
    check("anki_txt is word<TAB>definition",
          all("\t" in ln for ln in anki_txt.splitlines()),
          repr(anki_txt.splitlines()[-1:]))

    # -------------------------------------------------------------- filters
    check("query filter", {r["word"] for r in rows(query="ephem")} == {"ephemeral"})
    check("status filter", {r["word"] for r in rows(status="new")} ==
          {"ephemeral", "resilient", "ubiquitous", "noDefHere"})
    check("missing-only filter",
          {r["word"] for r in rows(missing_only=True)} == {"noDefHere"},
          str([r["word"] for r in rows(missing_only=True)]))
    check("unknown tag yields nothing", rows(tag="nope") == [])

    # ------------------------------------------------------------- sentences
    ephem = [r for r in rows(query="ephemeral")][0]
    check("sentence carried into export", "ephemeral" in (ephem["sentence"] or ""),
          str(ephem["sentence"]))

    # ------------------------------------------------------------- to disk
    out_dir = os.path.join(TMP, "out")
    os.makedirs(out_dir, exist_ok=True)
    result = export.export("md", out_dir)
    check("export into a directory names the file automatically",
          bool(result["path"]) and os.path.exists(result["path"]),
          str(result["path"]))
    check("file landed inside that directory",
          os.path.dirname(result["path"]) == out_dir, str(result["path"]))
    check("suffix is correct", result["path"].endswith(".md"), result["path"])
    written = open(result["path"], encoding="utf-8").read()
    check("file content matches", "ephemeral" in written)

    trailing = export.export("txt", os.path.join(TMP, "sub") + os.sep)["path"]
    check("trailing separator means directory",
          os.path.dirname(trailing) == os.path.join(TMP, "sub") and
          os.path.exists(trailing), trailing)

    nested = export.export("csv", os.path.join(TMP, "a", "b", "words"))["path"]
    check("creates nested directories",
          os.path.exists(nested) and nested.endswith(".csv"), nested)

    csv_file = export.export("csv", os.path.join(out_dir, "x"))["path"]
    check("csv path ends with .csv", csv_file.endswith(".csv"), csv_file)
    with open(csv_file, "rb") as fh:
        check("csv written as utf-8-sig for Excel",
              fh.read(3) == b"\xef\xbb\xbf")

    check("filename suggestion is timestamped",
          export.suggest_filename("csv").startswith("wordgrab-"))

    try:
        export.render("nope", rows())
        check("unknown format raises", False)
    except ValueError as exc:
        check("unknown format raises", "unknown format" in str(exc))

    # ------------------------------------------------------------------ API
    srv = server.ApiServer("127.0.0.1", 8812)
    srv.start()
    try:
        def get(path):
            try:
                with urllib.request.urlopen(f"http://127.0.0.1:8812{path}",
                                            timeout=10) as resp:
                    return resp.status, dict(resp.headers), resp.read().decode("utf-8")
            except urllib.error.HTTPError as exc:
                return exc.code, dict(exc.headers), exc.read().decode("utf-8")

        status, headers, body = get("/export?format=md")
        check("GET /export md", status == 200 and "markdown" in headers["Content-Type"])
        check("GET /export body", "ephemeral" in body)

        status, headers, body = get("/export?format=csv")
        check("GET /export csv", status == 200 and body.startswith("word,"))

        status, headers, body = get("/export?format=json")
        check("GET /export json", status == 200
              and json.loads(body)["count"] == 4)

        status, headers, body = get("/export?format=md&download=1")
        check("download header",
              status == 200 and "attachment" in headers.get("Content-Disposition", ""),
              headers.get("Content-Disposition", ""))

        status, _, body = get("/export?q=ephem")
        check("GET /export honours q", status == 200 and body.count("|") < 40,
              str(len(body)))

        status, _, body = get("/export?missing=1&format=txt")
        check("GET /export missing filter", status == 200 and body.strip() == "noDefHere",
              body.strip())

        status, _, body = get("/export?format=bogus")
        check("GET /export rejects bad format", status == 400, str(status))
    finally:
        srv.stop()

    verify()
    print()
    print("ALL PASS" if not failures else "FAILURES: " + ", ".join(failures))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
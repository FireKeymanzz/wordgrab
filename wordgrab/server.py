from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from . import db, dictionary, export
from .config import HOST, PORT, load_config

CORS_HEADERS = {
    "Access-Control-Allow-Origin": "*",
    "Access-Control-Allow-Methods": "GET, POST, PUT, DELETE, OPTIONS",
    "Access-Control-Allow-Headers": "Content-Type, X-WordGrab-Source",
    "Access-Control-Max-Age": "86400",
}


class Handler(BaseHTTPRequestHandler):
    server_version = "WordGrab/0.1"

    def log_message(self, fmt, *args):  # silence per-request logging
        pass

    # ---------------------------------------------------------------- helpers
    def _cors(self) -> None:
        for k, v in CORS_HEADERS.items():
            self.send_header(k, v)

    def _json(self, payload, status: int = 200) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self._cors()
        self.end_headers()
        self.wfile.write(body)

    def _body(self) -> dict:
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            return {}
        if length <= 0:
            return {}
        raw = self.rfile.read(length)
        try:
            return json.loads(raw.decode("utf-8"))
        except Exception:
            return {}

    # ----------------------------------------------------------------- routes
    def do_OPTIONS(self):  # noqa: N802
        self.send_response(204)
        self._cors()
        self.end_headers()

    def do_GET(self):  # noqa: N802
        parsed = urlparse(self.path)
        route = parsed.path.rstrip("/") or "/"
        qs = parse_qs(parsed.query)
        one = lambda k, d=None: (qs.get(k) or [d])[0]  # noqa: E731

        if route in ("/", "/health"):
            self._json({"ok": True, "service": "wordgrab", **db.stats()})
            return

        if route == "/words":
            rows = db.search_words(
                one("q", "") or "", int(one("limit", 100)), int(one("offset", 0))
            )
            self._json({"words": [self._row(r) for r in rows]})
            return

        if route.startswith("/words/"):
            tail = route[len("/words/"):]
            try:
                row = db.get_word_by_id(int(tail))
            except ValueError:
                self._json({"error": "bad id"}, 400)
                return
            self._json({"word": self._row(row) if row else None})
            return

        if route == "/review/queue":
            rows = db.review_queue(int(one("new", 10)), int(one("due", 30)))
            self._json({
                "queue": [self._row(r) for r in rows],
                "levels": [{"level": i, "name": db.LEVEL_NAMES[i], "label": db.LEVEL_LABELS[i]}
                           for i in range(4)],
            })
            return

        if route == "/review/due":
            rows = db.due_words(int(one("limit", 30)))
            self._json({"due": [self._row(r) for r in rows]})
            return

        if route == "/stats":
            self._json(db.stats())
            return

        if route == "/captures":
            rows = db.recent_captures(int(one("limit", 50)))
            self._json(
                {
                    "captures": [
                        {
                            "word": r["word"],
                            "app": r["app"],
                            "source": r["source"],
                            "url": r["url"],
                            "sentence": r["sentence"],
                            "method": r["method"],
                            "created_at": r["created_at"],
                        }
                        for r in rows
                    ]
                }
            )
            return

        if route == "/export":
            fmt = one("format", "csv")
            try:
                payload = export.export(
                    fmt, None,
                    query=one("q", "") or "",
                    status=one("status") or None,
                    tag=one("tag") or None,
                    missing_only=one("missing", "") in ("1", "true", "yes"),
                )
            except ValueError as exc:
                self._json({"error": str(exc)}, 400)
                return
            download = one("download", "") in ("1", "true", "yes")
            self._send_text(
                payload["content"], download,
                export.CONTENT_TYPES.get(fmt, "text/plain"),
                export.suggest_filename(fmt),
            )
            return

        self._json({"error": "not found", "path": route}, 404)

    def _send_text(self, body: str, download: bool, content_type: str,
                   filename: str) -> None:
        raw = body.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type",
                         "application/octet-stream" if download else content_type)
        self.send_header("Content-Length", str(len(raw)))
        if download:
            self.send_header(
                "Content-Disposition",
                f'attachment; filename="{filename or export.suggest_filename("csv")}"')
        self._cors()
        self.end_headers()
        self.wfile.write(raw)

    def do_POST(self):  # noqa: N802
        parsed = urlparse(self.path)
        route = parsed.path.rstrip("/") or "/"
        data = self._body()

        if route == "/add":
            word = (data.get("word") or "").strip()
            if not word:
                self._json({"error": "word required"}, 400)
                return
            try:
                result = db.add_word(
                    word,
                    sentence=data.get("sentence"),
                    source=data.get("source"),
                    url=data.get("url"),
                    app=data.get("app"),
                    method=data.get("method") or "api",
                )
            except ValueError as exc:
                self._json({"error": str(exc)}, 400)
                return
            want_lookup = data.get("lookup")
            if want_lookup is None:
                want_lookup = load_config().get("auto_lookup", True)
            if want_lookup:
                if data.get("lookup"):  # caller asked synchronously
                    hit = dictionary.lookup(result["word"])
                    if hit:
                        db.update_word(
                            result["id"],
                            definition=hit.get("definition"),
                            phonetic=hit.get("phonetic"),
                            example=hit.get("example"),
                        )
                        result.update(
                            definition=hit.get("definition"),
                            phonetic=hit.get("phonetic"),
                            example=hit.get("example"),
                        )
                elif not result.get("definition"):
                    dictionary.enrich_in_background(result["id"], result["word"])
            self._json({"ok": True, **result})
            return

        if route.startswith("/review/"):
            tail = route[len("/review/"):]
            # level 优先，其次兼容旧的 grade；值可以是 0/1/2/3 或 '认识' 这类名字
            payload_grade = data.get("level", data.get("grade", db.LEVEL_FAMILIAR))
            try:
                word_id = int(tail)
                grade = db.parse_level(payload_grade)
            except (ValueError, TypeError):
                self._json({"error": "bad payload"}, 400)
                return
            try:
                self._json({"ok": True, **db.schedule(word_id, grade)})
            except KeyError:
                self._json({"error": "not found"}, 404)
            return

        if route == "/words/update":
            try:
                db.update_word(int(data["id"]), **data)
            except (KeyError, ValueError):
                self._json({"error": "bad payload"}, 400)
                return
            self._json({"ok": True})
            return

        if route == "/words/delete":
            try:
                db.delete_word(int(data["id"]))
            except (KeyError, ValueError):
                self._json({"error": "bad payload"}, 400)
                return
            self._json({"ok": True})
            return

        if route == "/lookup":
            hit = dictionary.lookup(data.get("word", ""), force=bool(data.get("force")))
            self._json({"result": hit})
            return

        if route == "/backfill":
            limit = int(data.get("limit", 100))
            th = threading.Thread(
                target=dictionary.backfill_missing, args=(limit,), daemon=True
            )
            th.start()
            self._json({"ok": True, "started": True, "limit": limit})
            return

        self._json({"error": "not found", "path": route}, 404)

    def _row(self, row) -> dict:
        return {
            "id": row["id"],
            "word": row["word"],
            "definition": row["definition"],
            "phonetic": row["phonetic"],
            "example": row["example"],
            "note": row["note"],
            "tag": row["tag"],
            "hits": row["hits"],
            "status": row["status"],
            "reps": row["reps"],
            "lapses": row["lapses"],
            "interval_days": row["interval_days"],
            "due_at": row["due_at"],
            "created_at": row["created_at"],
            "last_seen_at": row["last_seen_at"],
            "source": row["source"],
        }


class PortBusy(Exception):
    def __init__(self, host: str, port: int) -> None:
        super().__init__(f"{host}:{port} is already in use")
        self.host = host
        self.port = port


class ApiServer:
    def __init__(self, host: str = HOST, port: int = PORT) -> None:
        self.host = host
        self.port = port
        self.httpd: ThreadingHTTPServer | None = None

    def start(self, fallback_ports: tuple[int, ...] = ()) -> int:
        self.httpd = ThreadingHTTPServer.allow_reuse_address = False
        candidates = (self.port, *fallback_ports)
        last: OSError | None = None
        for index, port in enumerate(candidates):
            try:
                self.httpd = ThreadingHTTPServer((self.host, port), Handler)
            except OSError as exc:
                last = exc
                continue
            self.port = port
            self.httpd.daemon_threads = True
            threading.Thread(target=self.httpd.serve_forever, name="api", daemon=True).start()
            return self.port
        if last is not None:
            raise PortBusy(self.host, self.port) from last
        raise PortBusy(self.host, self.port)

    def stop(self) -> None:
        if self.httpd:
            self.httpd.shutdown()
            self.httpd.server_close()
            self.httpd = None
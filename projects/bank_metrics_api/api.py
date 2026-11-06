"""HTTP JSON API на стандартной библиотеке: без фреймворков и зависимостей.

Реализованы вещи, которые обычно спрашивают про прод-API: пагинация и
фильтры, ETag/304, коды ответов 400/404/405, healthcheck, access-log одной
строкой. Тот же контракт переносится на FastAPI/Flask без изменения клиентов.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import sys
from datetime import date
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from . import store

MAX_LIMIT = 200


class ApiError(Exception):
    def __init__(self, status: HTTPStatus, message: str):
        super().__init__(message)
        self.status = status
        self.message = message


def _parse_date(value: str, name: str) -> str:
    try:
        return date.fromisoformat(value).isoformat()
    except ValueError as exc:
        raise ApiError(HTTPStatus.BAD_REQUEST, f"параметр {name} должен быть датой YYYY-MM-DD") from exc


def _parse_int(values: dict[str, list[str]], name: str, default: int, maximum: int) -> int:
    raw = values.get(name, [str(default)])[0]
    try:
        number = int(raw)
    except ValueError as exc:
        raise ApiError(HTTPStatus.BAD_REQUEST, f"параметр {name} должен быть целым") from exc
    if number < 0 or number > maximum:
        raise ApiError(HTTPStatus.BAD_REQUEST, f"параметр {name} вне диапазона 0..{maximum}")
    return number


def _parse_float(values: dict[str, list[str]], name: str, default: float) -> float:
    raw = values.get(name, [str(default)])[0]
    try:
        return float(raw)
    except ValueError as exc:
        raise ApiError(HTTPStatus.BAD_REQUEST, f"параметр {name} должен быть числом") from exc


class ApiHandler(BaseHTTPRequestHandler):
    server_version = "bank-metrics-api/1.0"
    protocol_version = "HTTP/1.1"

    db: sqlite3.Connection  # устанавливается фабрикой

    def log_message(self, fmt: str, *args) -> None:  # noqa: A003 - API базового класса
        sys.stderr.write(
            json.dumps(
                {
                    "ts": self.log_date_time_string(),
                    "method": self.command,
                    "path": self.path,
                    "status": getattr(self, "_status", 0),
                    "client": self.client_address[0],
                },
                ensure_ascii=False,
            )
            + "\n"
        )

    # -- маршрутизация -----------------------------------------------------
    def do_GET(self) -> None:  # noqa: N802 - имя из BaseHTTPRequestHandler
        parsed = urlparse(self.path)
        query = parse_qs(parsed.query)
        path = parsed.path.rstrip("/") or "/"
        try:
            payload = self.route(path, query)
        except ApiError as exc:
            self._send_json(exc.status, {"error": exc.message})
            return
        except sqlite3.Error as exc:
            self._send_json(HTTPStatus.INTERNAL_SERVER_ERROR, {"error": f"ошибка БД: {exc}"})
            return
        self._send_json(HTTPStatus.OK, payload)

    def do_POST(self) -> None:  # noqa: N802
        self._send_json(HTTPStatus.METHOD_NOT_ALLOWED, {"error": "только GET"})

    def route(self, path: str, query: dict[str, list[str]]) -> dict:
        if path == "/health":
            self.db.execute("SELECT 1").fetchone()
            return {"status": "ok", "database": "ok"}

        if path == "/clients":
            segment = query.get("segment", [None])[0]
            limit = _parse_int(query, "limit", 10, MAX_LIMIT)
            offset = _parse_int(query, "offset", 0, 10_000)
            return store.list_clients(self.db, segment, limit, offset)

        if path.startswith("/clients/") and path.endswith("/turnover"):
            client_id = path[len("/clients/"): -len("/turnover")]
            if store.get_client(self.db, client_id) is None:
                raise ApiError(HTTPStatus.NOT_FOUND, f"клиент {client_id} не найден")
            date_from = _parse_date(query.get("from", ["2026-10-01"])[0], "from")
            date_to = _parse_date(query.get("to", ["2026-11-06"])[0], "to")
            if date_from > date_to:
                raise ApiError(HTTPStatus.BAD_REQUEST, "from не может быть позже to")
            return store.turnover_for_client(self.db, client_id, date_from, date_to)

        if path == "/portfolio":
            return store.portfolio_summary(self.db, _parse_float(query, "min_debt", 0.0))

        if path == "/dq":
            return store.dq_status(self.db)

        raise ApiError(HTTPStatus.NOT_FOUND, f"маршрут {path} не найден")

    # -- ответ -------------------------------------------------------------
    def _send_json(self, status: HTTPStatus, payload: dict) -> None:
        body = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
        etag = '"' + hashlib.sha256(body).hexdigest()[:16] + '"'
        if status == HTTPStatus.OK and self.headers.get("If-None-Match") == etag:
            self._status = 304
            self.send_response(HTTPStatus.NOT_MODIFIED)
            self.send_header("ETag", etag)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        self._status = int(status)
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        if status == HTTPStatus.OK:
            self.send_header("ETag", etag)
        self.end_headers()
        self.wfile.write(body)


def make_server(db_path: str, port: int = 0, seed_data: bool = True) -> ThreadingHTTPServer:
    connection = store.connect(db_path)
    if seed_data:
        store.seed(connection)
    handler = type("BoundApiHandler", (ApiHandler,), {"db": connection})
    server = ThreadingHTTPServer(("127.0.0.1", port), handler)
    server.daemon_threads = True
    return server

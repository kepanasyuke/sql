"""HTTP-приложение: отдаёт страницу-песочницу и API для линтера и запросов."""

from __future__ import annotations

import json
import sqlite3
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, unquote, urlparse

from . import db, query, rules

STATIC_DIR = Path(__file__).parent / "static"
JSON_TYPE = "application/json; charset=utf-8"
HTML_TYPE = "text/html; charset=utf-8"
MAX_BODY = 64 * 1024
MAX_CODE = 32 * 1024


class ApiError(Exception):
    def __init__(self, status: HTTPStatus, message: str):
        super().__init__(message)
        self.status = status
        self.message = message


def _json(payload: dict, status: HTTPStatus = HTTPStatus.OK) -> tuple[int, bytes, str]:
    return int(status), json.dumps(payload, ensure_ascii=False).encode("utf-8"), JSON_TYPE


def _error(status: HTTPStatus, message: str) -> tuple[int, bytes, str]:
    return _json({"error": message}, status)


def _int_param(values: dict[str, list[str]], name: str, default: int, maximum: int) -> int:
    try:
        number = int(values.get(name, [str(default)])[0])
    except ValueError as exc:
        raise ApiError(HTTPStatus.BAD_REQUEST, f"параметр {name} должен быть целым") from exc
    if number < 1 or number > maximum:
        raise ApiError(HTTPStatus.BAD_REQUEST, f"параметр {name} вне диапазона 1..{maximum}")
    return number


class ApiHandler(BaseHTTPRequestHandler):
    server_version = "sql-lint/1.0"
    protocol_version = "HTTP/1.1"
    db_path = ":memory:"
    seed_on_open = True

    # -- утилиты -----------------------------------------------------------
    def log_message(self, fmt: str, *args: Any) -> None:  # noqa: A003 - API базового класса
        pass

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.db_path, check_same_thread=False)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA query_only = ON")
        return connection

    def _send(self, status: int, body: bytes, content_type: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _drain(self, length: int) -> None:
        """Дочитывает тело, чтобы клиент получил ответ 413, а не сброс соединения."""
        remaining = length
        while remaining > 0:
            chunk = self.rfile.read(min(64 * 1024, remaining))
            remaining = remaining - len(chunk) if chunk else 0

    def _read_json(self) -> dict:
        length = int(self.headers.get("Content-Length") or 0)
        if length > MAX_BODY:
            self._drain(length)
            raise ApiError(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, f"тело больше {MAX_BODY} байт")
        raw = self.rfile.read(length) if length else b"{}"
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ApiError(HTTPStatus.BAD_REQUEST, "тело должно быть JSON") from exc
        if not isinstance(payload, dict):
            raise ApiError(HTTPStatus.BAD_REQUEST, "ожидался JSON-объект")
        return payload

    # -- разбор запроса ----------------------------------------------------
    def _route_get(self, path: str, params: dict[str, list[str]]) -> tuple[int, bytes, str]:
        with self._connect() as connection:
            if path in ("/", "/index.html"):
                page = (STATIC_DIR / "app.html").read_text(encoding="utf-8")
                result = (int(HTTPStatus.OK), page.encode("utf-8"), HTML_TYPE)
            elif path == "/api/health":
                result = _json({"status": "ok", "version": "1.0.0", "tables": len(db.table_counts(connection))})
            elif path == "/api/schema":
                result = _json({"tables": db.tables(connection)})
            elif path.startswith("/api/table/"):
                name = unquote(path[len("/api/table/"):])
                result = _table(connection, name, _int_param(params, "limit", 20, 200))
            elif path == "/favicon.ico":
                result = (int(HTTPStatus.NO_CONTENT), b"", "image/x-icon")
            else:
                result = _error(HTTPStatus.NOT_FOUND, f"маршрут {path} не найден")
        return result

    def _route_post(self, path: str) -> tuple[int, bytes, str]:
        payload = self._read_json()
        code = payload.get("code") or ""
        if isinstance(code, str) and len(code) > MAX_CODE:
            raise ApiError(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, f"код больше {MAX_CODE} символов")
        with self._connect() as connection:
            if path == "/api/lint":
                result = _lint_response(code, payload)
            elif path == "/api/query":
                result = _query_response(connection, payload)
            elif path == "/api/analyze":
                result = _analyze_response(connection, code, payload)
            else:
                result = _error(HTTPStatus.NOT_FOUND, f"маршрут {path} не найден")
        return result

    # -- HTTP --------------------------------------------------------------
    def do_GET(self) -> None:  # noqa: N802 - имя из BaseHTTPRequestHandler
        parsed = urlparse(self.path)
        try:
            status, body, content_type = self._route_get(parsed.path, parse_qs(parsed.query))
        except ApiError as exc:
            status, body, content_type = _error(exc.status, exc.message)
        except (sqlite3.Error, OSError) as exc:
            status, body, content_type = _error(HTTPStatus.INTERNAL_SERVER_ERROR, f"ошибка сервера: {exc}")
        self._send(status, body, content_type)

    def do_POST(self) -> None:  # noqa: N802
        path = urlparse(self.path).path
        try:
            status, body, content_type = self._route_post(path)
        except ApiError as exc:
            status, body, content_type = _error(exc.status, exc.message)
        except (sqlite3.Error, OSError) as exc:
            status, body, content_type = _error(HTTPStatus.INTERNAL_SERVER_ERROR, f"ошибка сервера: {exc}")
        self._send(status, body, content_type)

    def do_PUT(self) -> None:  # noqa: N802
        self._send(*_error(HTTPStatus.METHOD_NOT_ALLOWED, "поддерживаются только GET и POST"))

    def do_DELETE(self) -> None:  # noqa: N802
        self._send(*_error(HTTPStatus.METHOD_NOT_ALLOWED, "поддерживаются только GET и POST"))

    def do_HEAD(self) -> None:  # noqa: N802
        self._send(*_error(HTTPStatus.METHOD_NOT_ALLOWED, "HEAD не поддерживается"))


def _table(connection: sqlite3.Connection, name: str, limit: int) -> tuple[int, bytes, str]:
    try:
        payload = db.preview(connection, name, limit)
        result = _json(payload)
    except KeyError as exc:
        result = _error(HTTPStatus.NOT_FOUND, str(exc))
    return result


def _lint_response(code: str, payload: dict) -> tuple[int, bytes, str]:
    max_lines = payload.get("max_lines", rules.DEFAULT_MAX_LINES)
    implicit = bool(payload.get("allow_implicit_exit", False))
    try:
        violations = rules.lint_code(code, max_lines=int(max_lines), allow_implicit_exit=implicit)
    except rules.LintError as exc:
        result = _json({"error": exc.message, "line": exc.line, "column": exc.column}, HTTPStatus.BAD_REQUEST)
    else:
        result = _json({
            "ok": not violations,
            "violations": [item.as_dict() for item in violations],
            "summary": rules.summary(violations),
        })
    return result


def _query_response(connection: sqlite3.Connection, payload: dict) -> tuple[int, bytes, str]:
    sql = payload.get("sql") or query.extract_sql(payload.get("code") or "") or ""
    if not sql:
        empty = query.QueryResult(sql="", error="не найден SQL: передайте поле sql или код с запросом")
        result = _json(empty.as_dict(), HTTPStatus.BAD_REQUEST)
    else:
        outcome = query.run_query(
            connection,
            sql,
            limit=int(payload.get("limit") or query.DEFAULT_LIMIT),
            timeout_ms=int(payload.get("timeout_ms") or query.DEFAULT_TIMEOUT_MS),
        )
        result = _json(outcome.as_dict(), HTTPStatus.OK if outcome.ok else HTTPStatus.BAD_REQUEST)
    return result


def _analyze_response(connection: sqlite3.Connection, code: str, payload: dict) -> tuple[int, bytes, str]:
    try:
        violations = rules.lint_code(code)
    except rules.LintError as exc:
        result = _json({"error": exc.message, "line": exc.line, "column": exc.column}, HTTPStatus.BAD_REQUEST)
    else:
        sql = query.extract_sql(code) or ""
        outcome = query.run_query(connection, sql) if sql else None
        result = _json({
            "ok": not violations,
            "violations": [item.as_dict() for item in violations],
            "summary": rules.summary(violations),
            "sql": sql,
            "query": outcome.as_dict() if outcome is not None else None,
        })
    return result


def make_server(db_path: str, port: int = 0, seed_data: bool = True) -> ThreadingHTTPServer:
    if seed_data:
        connection = db.connect(db_path)
        if not db.table_counts(connection).get("client"):
            db.seed(connection)
        connection.close()
    handler = type("BoundApiHandler", (ApiHandler,), {"db_path": db_path})
    server = ThreadingHTTPServer(("127.0.0.1", port), handler)
    server.daemon_threads = True
    return server

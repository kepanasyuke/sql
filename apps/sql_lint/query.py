"""Безопасное выполнение запросов: только чтение, лимит строк, лимит времени.

Песочница должна давать данные, но не давать испортить базу: соединение
открывается в режиме ``query_only``, разрешены только ``SELECT`` и ``WITH``,
результат обрезается по лимиту, а долгий запрос прерывается по дедлайну.
"""

from __future__ import annotations

import ast
import re
import sqlite3
import time
from dataclasses import dataclass, field
from typing import Any

READ_PREFIX = re.compile(r"^\s*(?:--[^\n]*\n\s*)*(select|with)\b", re.IGNORECASE)
DEFAULT_LIMIT = 200
MAX_LIMIT = 1000
DEFAULT_TIMEOUT_MS = 1500
PROGRESS_STEPS = 2000


class QueryError(ValueError):
    """Запрос отклонён до выполнения."""


@dataclass
class QueryResult:
    sql: str
    columns: list[str] = field(default_factory=list)
    rows: list[dict[str, Any]] = field(default_factory=list)
    rowcount: int = 0
    duration_ms: float = 0.0
    truncated: bool = False
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.error is None

    def as_dict(self) -> dict:
        return {
            "ok": self.ok,
            "sql": self.sql,
            "columns": self.columns,
            "rows": self.rows,
            "rowcount": self.rowcount,
            "duration_ms": round(self.duration_ms, 2),
            "truncated": self.truncated,
            "error": self.error,
        }


def _strip_sql_comments(sql: str) -> str:
    without_block = re.sub(r"/\*.*?\*/", " ", sql, flags=re.DOTALL)
    return re.sub(r"--[^\n]*", " ", without_block)


def _has_several_statements(sql: str) -> bool:
    cleaned = sql.strip().rstrip(";")
    return ";" in cleaned


def validate(sql: str) -> str:
    text = (sql or "").strip()
    if not text:
        raise QueryError("запрос пустой")
    stripped = _strip_sql_comments(text).strip()
    if not READ_PREFIX.match(stripped):
        raise QueryError("разрешены только запросы SELECT и WITH")
    if _has_several_statements(stripped):
        raise QueryError("разрешён только один запрос за раз")
    return text.rstrip(";").strip()


def _is_sql_literal(value: str) -> bool:
    return bool(re.match(r"^\s*(select|with)\b", value, re.IGNORECASE))


def extract_sql(code: str) -> str | None:
    """Достаёт последний SQL-литерал из кода — так удобно «выполнить то, что в функции».

    Куски f-строк (``JoinedStr``) не считаются запросом: склеенная строка не
    гарантирует корректный SQL и не должна уходить в песочницу.
    """
    tree = None
    try:
        tree = ast.parse(code)
    except SyntaxError:
        tree = None
    inside_fstring: set[int] = set()
    for node in ast.walk(tree) if tree is not None else []:
        if isinstance(node, ast.JoinedStr):
            inside_fstring.update(id(child) for child in ast.walk(node))
    found = [
        node.value
        for node in (ast.walk(tree) if tree is not None else [])
        if isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and id(node) not in inside_fstring
        and _is_sql_literal(node.value)
    ]
    return found[-1] if found else None


def open_readonly(path: str) -> sqlite3.Connection:
    connection = sqlite3.connect(path, check_same_thread=False)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA query_only = ON")
    return connection


def run_query(
    connection: sqlite3.Connection,
    sql: str,
    limit: int = DEFAULT_LIMIT,
    timeout_ms: int = DEFAULT_TIMEOUT_MS,
) -> QueryResult:
    result = QueryResult(sql=sql)
    started = time.perf_counter()
    deadline = started + timeout_ms / 1000
    try:
        statement = validate(sql)
        rows = _fetch(connection, statement, limit, deadline)
        result.columns, result.rows, result.truncated = rows
        result.rowcount = len(result.rows)
    except (QueryError, sqlite3.Error) as exc:
        result.error = str(exc)
    finally:
        connection.set_progress_handler(None, 0)
        result.duration_ms = (time.perf_counter() - started) * 1000
    return result


def _fetch(
    connection: sqlite3.Connection,
    statement: str,
    limit: int,
    deadline: float,
) -> tuple[list[str], list[dict[str, Any]], bool]:
    capped = max(1, min(int(limit), MAX_LIMIT))
    connection.set_progress_handler(lambda: int(time.perf_counter() > deadline), PROGRESS_STEPS)
    cursor = connection.execute(statement)
    columns = [description[0] for description in cursor.description or ()]
    fetched = cursor.fetchmany(capped + 1)
    truncated = len(fetched) > capped
    rows = [dict(row) for row in fetched[:capped]]
    return columns, rows, truncated

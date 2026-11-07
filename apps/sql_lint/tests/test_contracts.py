"""Контракты JSON: поля ответов, типы значений, единый формат ошибок."""

from __future__ import annotations

import pytest

from apps.sql_lint.tests.helpers import request

TABLES = ["account", "client", "dq_run", "loan", "turnover"]


@pytest.fixture(scope="module")
def schema(api_url: str) -> dict:
    return request(f"{api_url}/api/schema")[1]


@pytest.mark.parametrize("table", TABLES)
def test_schema_entry_fields(schema: dict, table: str) -> None:
    entry = next(item for item in schema["tables"] if item["table"] == table)
    assert set(entry) == {"table", "columns", "rows"}


@pytest.mark.parametrize("table", TABLES)
def test_schema_column_fields(schema: dict, table: str) -> None:
    entry = next(item for item in schema["tables"] if item["table"] == table)
    assert all(set(column) == {"name", "type", "pk"} for column in entry["columns"])


@pytest.mark.parametrize("table", TABLES)
def test_schema_rows_is_integer(schema: dict, table: str) -> None:
    entry = next(item for item in schema["tables"] if item["table"] == table)
    assert isinstance(entry["rows"], int) and entry["rows"] > 0


@pytest.mark.parametrize("table", TABLES)
def test_preview_row_keys_match_columns(api_url: str, table: str) -> None:
    payload = request(f"{api_url}/api/table/{table}?limit=2")[1]
    for row in payload["rows"]:
        assert set(row) == set(payload["columns"])


def test_health_fields(api_url: str) -> None:
    payload = request(f"{api_url}/api/health")[1]
    assert set(payload) == {"status", "version", "tables"}


def test_schema_top_level_fields(api_url: str) -> None:
    payload = request(f"{api_url}/api/schema")[1]
    assert set(payload) == {"tables"}


def test_lint_fields(api_url: str) -> None:
    payload = request(f"{api_url}/api/lint", "POST", {"code": "def f():\n    return 1\n"})[1]
    assert set(payload) == {"ok", "violations", "summary"}


def test_analyze_fields(api_url: str) -> None:
    payload = request(f"{api_url}/api/analyze", "POST", {"code": "x = 1\n"})[1]
    assert set(payload) == {"ok", "violations", "summary", "sql", "query"}


def test_error_body_has_message(api_url: str) -> None:
    payload = request(f"{api_url}/api/table/missing")[1]
    assert set(payload) == {"error"}


def test_bad_request_body_has_message(api_url: str) -> None:
    payload = request(f"{api_url}/api/table/client?limit=abc")[1]
    assert set(payload) == {"error"}


def test_syntax_error_body_has_position(api_url: str) -> None:
    payload = request(f"{api_url}/api/lint", "POST", {"code": "def f(:\n"})[1]
    assert {"error", "line", "column"} == set(payload)


def test_content_length_matches_body(api_url: str) -> None:
    status, payload, headers = request(f"{api_url}/")
    body = payload["raw"].encode("utf-8")
    assert status == 200
    assert int(headers["Content-Length"]) == len(body)


def test_query_duration_is_number(api_url: str) -> None:
    payload = request(f"{api_url}/api/query", "POST", {"sql": "SELECT 1 AS one"})[1]
    assert isinstance(payload["duration_ms"], (int, float))


def test_query_truncated_is_bool(api_url: str) -> None:
    payload = request(f"{api_url}/api/query", "POST", {"sql": "SELECT client_id FROM client", "limit": 1})[1]
    assert payload["truncated"] is True


def test_lint_summary_is_object(api_url: str) -> None:
    payload = request(f"{api_url}/api/lint", "POST", {"code": "def f():\n    return 1\n"})[1]
    assert payload["summary"] == {}


def test_lint_ok_is_bool(api_url: str) -> None:
    payload = request(f"{api_url}/api/lint", "POST", {"code": "def f():\n    return 1\n"})[1]
    assert payload["ok"] is True

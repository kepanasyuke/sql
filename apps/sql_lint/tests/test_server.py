"""Тесты HTTP-приложения: маршруты, коды ответов, схема JSON, лимиты."""

from __future__ import annotations

import threading

import pytest

from apps.sql_lint.tests.helpers import request

TABLES = ["account", "client", "dq_run", "loan", "turnover"]

CLEAN_CODE = "def f():\n    return 1\n"
TWO_RETURNS = "def f(x):\n    if x:\n        return 1\n    return 2\n"
LONG_FUNCTION = "def f():\n    a = 1\n    b = 2\n    return b\n"
WITH_CONTINUE = "def f(xs):\n    for x in xs:\n        continue\n"
SYNTAX_ERROR = "def f(:\n    pass\n"


# --------------------------------------------------------------------------
# GET
# --------------------------------------------------------------------------

def test_index_returns_html(api_url: str) -> None:
    status, _, headers = request(f"{api_url}/")
    assert status == 200
    assert "text/html" in headers["Content-Type"]


def test_index_contains_buttons_and_scripts(api_url: str) -> None:
    status, payload, _ = request(f"{api_url}/")
    body = payload["raw"]
    assert status == 200
    assert "sql-lint" in body
    for marker in ("btn-analyze", "btn-lint", "btn-run", "btn-db", "/api/analyze"):
        assert marker in body


def test_index_is_served_for_slash_and_index_html(api_url: str) -> None:
    assert request(f"{api_url}/")[0] == 200
    assert request(f"{api_url}/index.html")[0] == 200


def test_health(api_url: str) -> None:
    status, payload, _ = request(f"{api_url}/api/health")
    assert status == 200
    assert payload["status"] == "ok"
    assert payload["version"] == "1.0.0"


def test_health_reports_tables(api_url: str) -> None:
    _, payload, _ = request(f"{api_url}/api/health")
    assert payload["tables"] == 5


def test_schema_returns_all_tables(api_url: str) -> None:
    status, payload, _ = request(f"{api_url}/api/schema")
    assert status == 200
    assert [item["table"] for item in payload["tables"]] == TABLES


@pytest.mark.parametrize("table", TABLES)
def test_schema_has_columns(api_url: str, table: str) -> None:
    _, payload, _ = request(f"{api_url}/api/schema")
    meta = next(item for item in payload["tables"] if item["table"] == table)
    assert meta["columns"]
    assert meta["rows"] > 0


@pytest.mark.parametrize("table", TABLES)
def test_table_preview(api_url: str, table: str) -> None:
    status, payload, _ = request(f"{api_url}/api/table/{table}")
    assert status == 200
    assert payload["table"] == table
    assert payload["rows"]


@pytest.mark.parametrize("table", TABLES)
def test_table_preview_columns(api_url: str, table: str) -> None:
    _, payload, _ = request(f"{api_url}/api/table/{table}?limit=3")
    assert set(payload["columns"]) == set(payload["rows"][0])


@pytest.mark.parametrize("table", TABLES)
@pytest.mark.parametrize("limit", [1, 5, 20])
def test_table_preview_limit(api_url: str, table: str, limit: int) -> None:
    _, payload, _ = request(f"{api_url}/api/table/{table}?limit={limit}")
    assert 0 < len(payload["rows"]) <= limit


@pytest.mark.parametrize("query", ["limit=0", "limit=999", "limit=abc", "limit=-1"])
def test_table_bad_limit(api_url: str, query: str) -> None:
    status, payload, _ = request(f"{api_url}/api/table/client?{query}")
    assert status == 400
    assert "limit" in payload["error"]


def test_unknown_table(api_url: str) -> None:
    status, payload, _ = request(f"{api_url}/api/table/missing")
    assert status == 404
    assert "missing" in payload["error"]


@pytest.mark.parametrize("path", ["/api/nope", "/api/table", "/missing"])
def test_unknown_route(api_url: str, path: str) -> None:
    status, payload, _ = request(f"{api_url}{path}")
    assert status == 404
    assert payload["error"]


def test_favicon_is_empty(api_url: str) -> None:
    status, _, _ = request(f"{api_url}/favicon.ico")
    assert status == 204


# --------------------------------------------------------------------------
# POST /api/lint
# --------------------------------------------------------------------------

LINT_CASES = [
    ("чистый", CLEAN_CODE, 200, True, []),
    ("два return", TWO_RETURNS, 200, False, ["single-exit", "small-function"]),
    ("длинная", LONG_FUNCTION, 200, False, ["small-function"]),
    ("continue", WITH_CONTINUE, 200, False, ["no-continue", "single-exit", "small-function"]),
    ("пусто", "", 200, True, []),
    ("без функций", "x = 1\n", 200, True, []),
]


@pytest.mark.parametrize("name,code,status,ok,rules_expected", LINT_CASES, ids=[case[0] for case in LINT_CASES])
def test_lint_status(api_url: str, name: str, code: str, status: int, ok: bool, rules_expected: list[str]) -> None:
    response_status, payload, _ = request(f"{api_url}/api/lint", "POST", {"code": code})
    assert response_status == status
    assert payload["ok"] is ok


@pytest.mark.parametrize("name,code,status,ok,rules_expected", LINT_CASES, ids=[case[0] for case in LINT_CASES])
def test_lint_rules_reported(api_url: str, name: str, code: str, status: int, ok: bool, rules_expected: list[str]) -> None:
    _, payload, _ = request(f"{api_url}/api/lint", "POST", {"code": code})
    assert sorted({item["rule"] for item in payload["violations"]}) == sorted(rules_expected)


def test_lint_syntax_error_is_400(api_url: str) -> None:
    status, payload, _ = request(f"{api_url}/api/lint", "POST", {"code": SYNTAX_ERROR})
    assert status == 400
    assert payload["line"] >= 1


def test_lint_violation_schema(api_url: str) -> None:
    _, payload, _ = request(f"{api_url}/api/lint", "POST", {"code": TWO_RETURNS})
    violation = payload["violations"][0]
    assert set(violation) == {"rule", "severity", "line", "column", "message", "function"}
    assert violation["function"] == "f"


def test_lint_max_lines_override(api_url: str) -> None:
    _, payload, _ = request(f"{api_url}/api/lint", "POST", {"code": LONG_FUNCTION, "max_lines": 5})
    assert payload["ok"] is True


def test_lint_summary_present(api_url: str) -> None:
    _, payload, _ = request(f"{api_url}/api/lint", "POST", {"code": LONG_FUNCTION})
    assert payload["summary"] == {"small-function": 1}


def test_lint_unicode_code(api_url: str) -> None:
    code = 'def проверить(x):\n    """Док."""\n    return x\n'
    status, payload, _ = request(f"{api_url}/api/lint", "POST", {"code": code})
    assert status == 200
    assert payload["violations"][0]["rule"] == "small-function"


# --------------------------------------------------------------------------
# POST /api/query
# --------------------------------------------------------------------------

QUERY_CASES = [
    ("sql поле", {"sql": "SELECT 1 AS one"}, 200, True),
    ("код с sql", {"code": 'q = "SELECT 2 AS two"'}, 200, True),
    ("нет sql", {"code": "x = 1"}, 400, False),
    ("запись", {"sql": "DELETE FROM client"}, 400, False),
    ("несуществующая таблица", {"sql": "SELECT * FROM missing"}, 400, False),
]


@pytest.mark.parametrize("name,payload,status,ok", QUERY_CASES, ids=[case[0] for case in QUERY_CASES])
def test_query_status(api_url: str, name: str, payload: dict, status: int, ok: bool) -> None:
    response_status, body, _ = request(f"{api_url}/api/query", "POST", payload)
    assert response_status == status
    assert body["ok"] is ok


@pytest.mark.parametrize("name,payload,status,ok", QUERY_CASES, ids=[case[0] for case in QUERY_CASES])
def test_query_payload_shape(api_url: str, name: str, payload: dict, status: int, ok: bool) -> None:
    _, body, _ = request(f"{api_url}/api/query", "POST", payload)
    assert {"ok", "columns", "rows", "rowcount", "duration_ms", "truncated", "error"} <= set(body)


def test_query_returns_data(api_url: str) -> None:
    _, body, _ = request(f"{api_url}/api/query", "POST", {"sql": "SELECT client_id FROM client ORDER BY client_id"})
    assert body["rows"][0]["client_id"] == "CL0000"


def test_query_limit_parameter(api_url: str) -> None:
    _, body, _ = request(f"{api_url}/api/query", "POST", {"sql": "SELECT client_id FROM client", "limit": 2})
    assert body["rowcount"] == 2


def test_query_extracts_sql_from_code(api_url: str) -> None:
    code = 'def f():\n    return run_sql("SELECT 42 AS answer")\n'
    _, body, _ = request(f"{api_url}/api/query", "POST", {"code": code})
    assert body["rows"][0]["answer"] == 42


# --------------------------------------------------------------------------
# POST /api/analyze
# --------------------------------------------------------------------------

def test_analyze_combines_lint_and_query(api_url: str) -> None:
    code = 'def f():\n    return run_sql("SELECT 7 AS seven")\n'
    status, body, _ = request(f"{api_url}/api/analyze", "POST", {"code": code})
    assert status == 200
    assert set(body) == {"ok", "violations", "summary", "sql", "query"}


def test_analyze_reports_both_parts(api_url: str) -> None:
    code = TWO_RETURNS + '\n\nq = "SELECT 1 AS one"\n'
    _, body, _ = request(f"{api_url}/api/analyze", "POST", {"code": code})
    assert body["ok"] is False
    assert body["query"]["rowcount"] == 1


def test_analyze_without_sql(api_url: str) -> None:
    _, body, _ = request(f"{api_url}/api/analyze", "POST", {"code": CLEAN_CODE})
    assert body["sql"] == ""
    assert body["query"] is None


def test_analyze_syntax_error(api_url: str) -> None:
    status, body, _ = request(f"{api_url}/api/analyze", "POST", {"code": SYNTAX_ERROR})
    assert status == 400
    assert "error" in body


# --------------------------------------------------------------------------
# Методы, тело, заголовки
# --------------------------------------------------------------------------

@pytest.mark.parametrize("method", ["PUT", "DELETE"])
def test_unsupported_methods(api_url: str, method: str) -> None:
    status, payload, _ = request(f"{api_url}/api/lint", method, {"code": ""})
    assert status == 405
    assert "GET" in payload["error"]


def test_head_is_not_allowed(api_url: str) -> None:
    status, _, _ = request(f"{api_url}/api/lint", "HEAD")
    assert status == 405


@pytest.mark.parametrize("body", [b"{not json", b"[]", b'"text"'])
def test_bad_json_body(api_url: str, body: bytes) -> None:
    status, payload, _ = request(f"{api_url}/api/lint", "POST", raw=body)
    assert status == 400


def test_large_code_is_rejected(api_url: str) -> None:
    status, payload, _ = request(f"{api_url}/api/lint", "POST", {"code": "x = 1\n" * 40_000})
    assert status == 413


def test_post_unknown_route(api_url: str) -> None:
    status, payload, _ = request(f"{api_url}/api/nope", "POST", {"code": ""})
    assert status == 404


def test_json_content_type(api_url: str) -> None:
    _, _, headers = request(f"{api_url}/api/health")
    assert "application/json" in headers["Content-Type"]


def test_cache_control(api_url: str) -> None:
    _, _, headers = request(f"{api_url}/api/schema")
    assert headers.get("Cache-Control") == "no-store"


def test_parallel_requests(api_url: str) -> None:
    results: list[int] = []

    def worker() -> None:
        results.append(request(f"{api_url}/api/health")[0])

    threads = [threading.Thread(target=worker) for _ in range(5)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert results == [200] * 5


def test_lint_allow_implicit_exit(api_url: str) -> None:
    status, payload, _ = request(f"{api_url}/api/lint", "POST", {"code": "def f():\n    pass\n", "allow_implicit_exit": True})
    assert status == 200
    assert payload["ok"] is True

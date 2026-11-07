"""Проверки безопасности песочницы: только чтение, никакого выполнения кода."""

from __future__ import annotations

import urllib.parse

import pytest

from apps.sql_lint.tests.helpers import request

WRITE_ATTEMPTS = [
    "INSERT INTO client (client_id, name, inn, segment) VALUES ('X', 'X', '1', 'МСБ')",
    "UPDATE client SET name = 'hacked'",
    "DELETE FROM client",
    "DROP TABLE client",
    "ALTER TABLE client ADD COLUMN hacked TEXT",
    "PRAGMA writable_schema = ON",
    "VACUUM",
    "ATTACH DATABASE 'x.db' AS x",
]


@pytest.mark.parametrize("sql", WRITE_ATTEMPTS)
def test_writes_are_rejected_by_api(api_url: str, sql: str) -> None:
    status, payload, _ = request(f"{api_url}/api/query", "POST", {"sql": sql})
    assert status == 400
    assert payload["ok"] is False


@pytest.mark.parametrize("sql", WRITE_ATTEMPTS)
def test_data_survives_write_attempts(api_url: str, sql: str) -> None:
    request(f"{api_url}/api/query", "POST", {"sql": sql})
    status, payload, _ = request(f"{api_url}/api/query", "POST", {"sql": "SELECT COUNT(*) AS c FROM client"})
    assert status == 200
    assert payload["rows"][0]["c"] == 120


@pytest.mark.parametrize(
    "path",
    ["/api/table/../client", "/api/table/%2e%2e%2fclient", "/api/table/..", "/api/table/%00"],
)
def test_path_traversal_is_not_a_table(api_url: str, path: str) -> None:
    status, payload, _ = request(f"{api_url}{path}")
    assert status == 404


@pytest.mark.parametrize(
    "table",
    ["client'; DROP TABLE client; --", "client OR 1=1", "sqlite_master"],
)
def test_injection_in_table_name(api_url: str, table: str) -> None:
    encoded = urllib.parse.quote(table, safe="")
    status, _, _ = request(f"{api_url}/api/table/{encoded}")
    assert status in (400, 404)


def test_code_is_not_executed(api_url: str) -> None:
    code = "import os\nos.system('echo hacked')\n\nq = 'SELECT 1 AS one'\n"
    status, payload, _ = request(f"{api_url}/api/analyze", "POST", {"code": code})
    assert status == 200
    assert payload["query"]["rows"][0]["one"] == 1


def test_importlib_code_is_not_executed(api_url: str) -> None:
    code = "__import__('sys').exit(3)\nq = 'SELECT 2 AS two'\n"
    status, payload, _ = request(f"{api_url}/api/lint", "POST", {"code": code})
    assert status == 200


def test_parameters_are_not_substituted_into_sql(api_url: str) -> None:
    sql = "SELECT '{}' AS literal"
    status, payload, _ = request(f"{api_url}/api/query", "POST", {"sql": sql})
    assert status == 200
    assert payload["rows"][0]["literal"] == "{}"

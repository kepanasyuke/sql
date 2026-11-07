"""Тесты выполнения запросов: только чтение, лимиты, таймауты, извлечение SQL."""

from __future__ import annotations

import pytest

from apps.sql_lint import query

QUERIES = [
    ("клиенты", "SELECT client_id, name FROM client ORDER BY client_id LIMIT 3"),
    ("счётчик", "SELECT COUNT(*) AS c FROM client"),
    ("группировка", "SELECT segment, COUNT(*) AS c FROM client GROUP BY segment"),
    ("join", "SELECT t.account_id, a.client_id FROM turnover t JOIN account a ON a.account_id = t.account_id LIMIT 5"),
    ("оконная функция", "SELECT account_id, dt, ROW_NUMBER() OVER (PARTITION BY account_id ORDER BY dt) AS rn FROM turnover LIMIT 5"),
    ("CTE", "WITH x AS (SELECT client_id FROM client LIMIT 5) SELECT * FROM x"),
    ("подзапрос", "SELECT client_id FROM client WHERE client_id IN (SELECT client_id FROM account) LIMIT 5"),
    ("union all", "SELECT 'a' AS x UNION ALL SELECT 'b'"),
    ("case", "SELECT CASE WHEN 1 = 1 THEN 'yes' ELSE 'no' END AS answer"),
    ("coalesce", "SELECT COALESCE(NULL, 5) AS v"),
    ("like", "SELECT client_id FROM client WHERE client_id LIKE 'CL00%' LIMIT 5"),
    ("between", "SELECT dt FROM turnover WHERE dt BETWEEN '2026-10-01' AND '2026-10-05' LIMIT 5"),
    ("order desc", "SELECT run_ts FROM dq_run ORDER BY run_ts DESC"),
    ("distinct", "SELECT DISTINCT segment FROM client"),
    ("having", "SELECT segment, COUNT(*) AS c FROM client GROUP BY segment HAVING COUNT(*) > 1"),
    ("left join", "SELECT c.client_id, l.product FROM client c LEFT JOIN loan l ON l.client_id = c.client_id LIMIT 5"),
    ("exists", "SELECT client_id FROM client c WHERE EXISTS (SELECT 1 FROM loan l WHERE l.client_id = c.client_id) LIMIT 5"),
    ("substr", "SELECT SUBSTR(client_id, 1, 2) AS prefix FROM client LIMIT 1"),
    ("round", "SELECT ROUND(1.234, 2) AS value"),
    ("avg", "SELECT AVG(balance) AS avg_balance FROM turnover"),
    ("min max", "SELECT MIN(dt) AS lo, MAX(dt) AS hi FROM turnover"),
    ("sum", "SELECT SUM(debit) AS total FROM turnover"),
    ("count distinct", "SELECT COUNT(DISTINCT segment) AS c FROM client"),
    ("окно sum", "SELECT dt, SUM(debit) OVER (PARTITION BY account_id) AS s FROM turnover LIMIT 5"),
    ("recursive", "WITH RECURSIVE n(x) AS (SELECT 1 UNION ALL SELECT x + 1 FROM n WHERE x < 5) SELECT x FROM n"),
    ("cast", "SELECT CAST(1 AS TEXT) AS text_value"),
    ("два join", "SELECT c.segment, COUNT(*) AS c FROM turnover t JOIN account a ON a.account_id = t.account_id JOIN client c ON c.client_id = a.client_id GROUP BY c.segment"),
    ("line comment", "-- комментарий\nSELECT 1 AS one"),
    ("block comment", "/* блок */ SELECT 1 AS one"),
    ("точка с запятой", "SELECT 1 AS one;"),
    ("переносы строк", "\n\nSELECT 1 AS one\n"),
    ("dq статусы", "SELECT status, COUNT(*) AS c FROM dq_run GROUP BY status"),
    ("портфель", "SELECT product, SUM(debt_amt) AS debt FROM loan GROUP BY product ORDER BY debt DESC"),
]

INVALID = [
    ("пустой", ""),
    ("пробелы", "   \n  "),
    ("insert", "INSERT INTO client (client_id) VALUES ('x')"),
    ("update", "UPDATE client SET name = 'x'"),
    ("delete", "DELETE FROM client"),
    ("drop", "DROP TABLE client"),
    ("create", "CREATE TABLE x (id INTEGER)"),
    ("pragma", "PRAGMA table_info(client)"),
    ("explain", "EXPLAIN SELECT 1"),
    ("два запроса", "SELECT 1; SELECT 2"),
    ("битый sql", "SELECT FROM"),
    ("нет таблицы", "SELECT * FROM missing_table"),
    ("cte с delete", "WITH x AS (SELECT 1) DELETE FROM client"),
]


@pytest.fixture()
def connection(sql_lint_db: str):
    connection = query.open_readonly(sql_lint_db)
    yield connection
    connection.close()


@pytest.mark.parametrize("name,sql", QUERIES, ids=[case[0] for case in QUERIES])
def test_valid_query_runs(connection, name: str, sql: str) -> None:
    result = query.run_query(connection, sql, limit=10)
    assert result.ok, result.error
    assert result.columns


@pytest.mark.parametrize("name,sql", QUERIES, ids=[case[0] for case in QUERIES])
def test_valid_query_result_shape(connection, name: str, sql: str) -> None:
    result = query.run_query(connection, sql, limit=10)
    payload = result.as_dict()
    assert set(payload) == {"ok", "sql", "columns", "rows", "rowcount", "duration_ms", "truncated", "error"}
    assert isinstance(payload["rows"], list)
    assert payload["rowcount"] == len(payload["rows"])


@pytest.mark.parametrize("name,sql", QUERIES, ids=[case[0] for case in QUERIES])
def test_limit_is_respected(connection, name: str, sql: str) -> None:
    result = query.run_query(connection, sql, limit=2)
    assert len(result.rows) <= 2


@pytest.mark.parametrize("name,sql", INVALID, ids=[case[0] for case in INVALID])
def test_invalid_query_is_rejected(connection, name: str, sql: str) -> None:
    result = query.run_query(connection, sql)
    assert result.ok is False
    assert result.error


@pytest.mark.parametrize("name,sql", INVALID, ids=[case[0] for case in INVALID])
def test_invalid_query_returns_no_rows(connection, name: str, sql: str) -> None:
    result = query.run_query(connection, sql)
    assert result.rows == []
    assert result.columns == []


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT 1",
        "  SELECT 1",
        "\nSELECT 1",
        "WITH x AS (SELECT 1) SELECT * FROM x",
        "-- комментарий\nSELECT 1",
        "SELECT 1;",
        "select 1",
        "SeLeCt 1",
    ],
)
def test_validate_accepts_read_queries(sql: str) -> None:
    assert query.validate(sql)


@pytest.mark.parametrize(
    "sql",
    ["", "  ", "INSERT INTO t VALUES (1)", "delete from t", "VACUUM", "SELECT 1; SELECT 2", "EXPLAIN SELECT 1"],
)
def test_validate_rejects_others(sql: str) -> None:
    with pytest.raises(query.QueryError):
        query.validate(sql)


def test_error_message_mentions_select_and_with(connection) -> None:
    assert "SELECT" in query.run_query(connection, "UPDATE client SET name='x'").error


def test_error_message_mentions_single_statement(connection) -> None:
    assert "один запрос" in query.run_query(connection, "SELECT 1; SELECT 2").error


def test_empty_query_error(connection) -> None:
    assert "пустой" in query.run_query(connection, "").error


@pytest.mark.parametrize("sql", ["INSERT INTO client (client_id) VALUES ('x')", "DELETE FROM client", "DROP TABLE client"])
def test_writes_are_blocked(connection, sql: str) -> None:
    result = query.run_query(connection, sql)
    assert result.ok is False


def test_query_only_pragma_is_on(connection) -> None:
    assert connection.execute("PRAGMA query_only").fetchone()[0] == 1


def test_truncation_flag(connection) -> None:
    result = query.run_query(connection, "SELECT client_id FROM client", limit=1)
    assert result.truncated is True
    assert result.rowcount == 1


def test_no_truncation_for_small_result(connection) -> None:
    result = query.run_query(connection, "SELECT 1 AS one", limit=10)
    assert result.truncated is False


def test_limit_is_capped(connection) -> None:
    result = query.run_query(connection, "SELECT client_id FROM client", limit=10_000)
    assert result.rowcount <= query.MAX_LIMIT


@pytest.mark.parametrize("limit", [1, 5, 25, 200])
def test_various_limits(connection, limit: int) -> None:
    result = query.run_query(connection, "SELECT client_id FROM client", limit=limit)
    assert result.ok
    assert result.rowcount <= limit


def test_timeout_interrupts_long_query(connection) -> None:
    slow = "WITH RECURSIVE c(x) AS (SELECT 1 UNION ALL SELECT x + 1 FROM c) SELECT COUNT(*) FROM c"
    result = query.run_query(connection, slow, timeout_ms=40)
    assert result.ok is False
    assert "interrupt" in result.error.lower() or "прер" in result.error.lower()


def test_duration_is_measured(connection) -> None:
    result = query.run_query(connection, "SELECT 1 AS one")
    assert result.duration_ms >= 0


MORE_QUERIES = [
    ("порядок по двум", "SELECT segment, client_id FROM client ORDER BY segment, client_id LIMIT 5"),
    ("вложенный case", "SELECT CASE WHEN COUNT(*) > 0 THEN 1 ELSE 0 END AS flag FROM client"),
    ("strftime", "SELECT strftime('%Y', dt) AS year FROM turnover LIMIT 1"),
    ("in списком", "SELECT client_id FROM client WHERE segment IN ('МСБ', 'Госсектор') LIMIT 3"),
    ("not in", "SELECT client_id FROM client WHERE client_id NOT IN (SELECT client_id FROM loan) LIMIT 3"),
    ("group by двух", "SELECT segment, manager, COUNT(*) AS c FROM client GROUP BY segment, manager"),
    ("union", "SELECT 1 AS x UNION SELECT 2"),
    ("подзапрос в select", "SELECT client_id, (SELECT COUNT(*) FROM loan l WHERE l.client_id = c.client_id) AS loans FROM client c LIMIT 3"),
]


@pytest.mark.parametrize("name,sql", MORE_QUERIES, ids=[case[0] for case in MORE_QUERIES])
def test_additional_queries(connection, name: str, sql: str) -> None:
    result = query.run_query(connection, sql, limit=5)
    assert result.ok, result.error


EXTRACT_CASES = [
    ("простая функция", 'def f():\n    return run_sql("SELECT 1")\n', "SELECT 1"),
    ("with", 'q = """WITH x AS (SELECT 1) SELECT * FROM x"""\n', "WITH x AS (SELECT 1) SELECT * FROM x"),
    ("не sql", 'name = "hello"\n', None),
    ("две константы", 'a = "SELECT 1"\nb = "SELECT 2"\n', "SELECT 2"),
    ("без sql", "x = 1\n", None),
    ("синтаксис", "def f(:\n", None),
    ("нижний регистр", 'q = "select 1"\n', "select 1"),
    ("ведущий перенос", 'q = """\\nSELECT 1"""\n', "\nSELECT 1"),
    ("f-string не считается", 'q = f"SELECT {1}"\n', None),
    ("вложенная функция", 'def f():\n    def g():\n        return "SELECT 9"\n    return g()\n', "SELECT 9"),
]


@pytest.mark.parametrize("name,code,expected", EXTRACT_CASES, ids=[case[0] for case in EXTRACT_CASES])
def test_extract_sql(name: str, code: str, expected: str | None) -> None:
    assert query.extract_sql(code) == expected

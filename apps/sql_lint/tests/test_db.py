"""Тесты схемы и наполнения демо-базы."""

from __future__ import annotations

import sqlite3

import pytest

from apps.sql_lint import db

TABLES = ["account", "client", "dq_run", "loan", "turnover"]


@pytest.fixture(scope="module")
def connection(tmp_path_factory):
    path = tmp_path_factory.mktemp("db") / "demo.sqlite3"
    connection = db.connect(path)
    db.seed(connection)
    yield connection
    connection.close()


@pytest.mark.parametrize("table", TABLES)
def test_table_exists(connection, table: str) -> None:
    assert connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?", (table,)
    ).fetchone()


@pytest.mark.parametrize("table", TABLES)
def test_table_not_empty(connection, table: str) -> None:
    assert db.table_counts(connection)[table] > 0


@pytest.mark.parametrize("table", TABLES)
def test_table_columns(connection, table: str) -> None:
    meta = next(item for item in db.tables(connection) if item["table"] == table)
    assert len(meta["columns"]) >= 3


@pytest.mark.parametrize("table", TABLES)
def test_table_has_primary_key(connection, table: str) -> None:
    meta = next(item for item in db.tables(connection) if item["table"] == table)
    assert any(column["pk"] for column in meta["columns"])


@pytest.mark.parametrize("table", TABLES)
def test_preview_returns_rows(connection, table: str) -> None:
    payload = db.preview(connection, table, limit=5)
    assert payload["table"] == table
    assert 0 < len(payload["rows"]) <= 5


@pytest.mark.parametrize("table", TABLES)
def test_preview_columns_match_rows(connection, table: str) -> None:
    payload = db.preview(connection, table, limit=3)
    assert set(payload["columns"]) == set(payload["rows"][0])


@pytest.mark.parametrize("table", TABLES)
@pytest.mark.parametrize("limit", [1, 7, 30])
def test_preview_limit(connection, table: str, limit: int) -> None:
    payload = db.preview(connection, table, limit=limit)
    assert len(payload["rows"]) <= limit


def test_preview_unknown_table(connection) -> None:
    with pytest.raises(KeyError):
        db.preview(connection, "missing")


def test_table_counts_keys(connection) -> None:
    assert sorted(db.table_counts(connection)) == TABLES


def test_seed_is_idempotent(connection) -> None:
    before = db.table_counts(connection)
    db.seed(connection)
    assert db.table_counts(connection) == before


def test_seed_clients_count(connection) -> None:
    assert connection.execute("SELECT COUNT(*) FROM client").fetchone()[0] == 120


def test_seed_dq_rows(connection) -> None:
    assert connection.execute("SELECT COUNT(*) FROM dq_run").fetchone()[0] == 4


def test_turnover_has_all_accounts(connection) -> None:
    assert connection.execute(
        "SELECT COUNT(DISTINCT account_id) FROM turnover"
    ).fetchone()[0] == connection.execute("SELECT COUNT(*) FROM account").fetchone()[0]


def test_balance_is_positive(connection) -> None:
    assert connection.execute("SELECT COUNT(*) FROM turnover WHERE balance < 0").fetchone()[0] == 0


def test_segments_are_from_domain(connection) -> None:
    rows = {row[0] for row in connection.execute("SELECT DISTINCT segment FROM client")}
    assert rows <= set(db.SEGMENTS)


def test_loans_have_limits(connection) -> None:
    assert connection.execute("SELECT COUNT(*) FROM loan WHERE limit_amt <= 0").fetchone()[0] == 0


def test_indexes_exist(connection) -> None:
    names = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'index'")}
    assert "idx_turnover_dt" in names
    assert "idx_account_client" in names


def test_connect_creates_parent_dir(tmp_path) -> None:
    path = tmp_path / "nested" / "dir" / "db.sqlite3"
    connection = db.connect(path)
    connection.close()
    assert path.exists()


def test_connect_returns_rows_as_dicts(connection) -> None:
    row = connection.execute("SELECT client_id, name FROM client LIMIT 1").fetchone()
    assert dict(row)["client_id"].startswith("CL")


def test_seed_returns_counts(connection) -> None:
    counts = db.seed(connection)
    assert counts["client"] == 120
    assert counts["turnover"] > 0


def test_preview_limit_cannot_be_negative(connection) -> None:
    payload = db.preview(connection, "client", limit=0)
    assert payload["rows"] == []

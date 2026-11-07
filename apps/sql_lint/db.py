"""Демонстрационная база: клиенты банка, счета, обороты, портфель, DQ-прогоны.

Данные детерминированы (seed), поэтому одинаковы на любой машине — это важно
для тестов сервера и для скриншотов.
"""

from __future__ import annotations

import random
import sqlite3
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS client (
    client_id TEXT PRIMARY KEY,
    name      TEXT NOT NULL,
    inn       TEXT NOT NULL,
    segment   TEXT NOT NULL,
    manager   TEXT
);
CREATE TABLE IF NOT EXISTS account (
    account_id TEXT PRIMARY KEY,
    client_id  TEXT NOT NULL,
    currency   TEXT NOT NULL,
    opened_dt  TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS turnover (
    account_id TEXT NOT NULL,
    dt         TEXT NOT NULL,
    debit      REAL NOT NULL,
    credit     REAL NOT NULL,
    balance    REAL NOT NULL,
    PRIMARY KEY (account_id, dt)
);
CREATE TABLE IF NOT EXISTS loan (
    client_id    TEXT NOT NULL,
    product      TEXT NOT NULL,
    limit_amt    REAL NOT NULL,
    debt_amt     REAL NOT NULL,
    overdue_amt  REAL NOT NULL,
    PRIMARY KEY (client_id, product)
);
CREATE TABLE IF NOT EXISTS dq_run (
    run_id     TEXT PRIMARY KEY,
    check_id   TEXT NOT NULL,
    status     TEXT NOT NULL,
    metric     REAL,
    run_ts     TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_turnover_dt ON turnover (dt);
CREATE INDEX IF NOT EXISTS idx_account_client ON account (client_id);
"""

SEGMENTS = ["МСБ", "Средний бизнес", "Крупный бизнес", "Госсектор"]
PRODUCTS = ["Овердрафт", "Кредитная линия", "Торговое финансирование", "Депозит"]
CURRENCIES = ["RUB", "RUB", "RUB", "USD", "EUR"]


def connect(path: str | Path) -> sqlite3.Connection:
    if str(path) != ":memory:":
        Path(path).parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(str(path), check_same_thread=False)
    connection.row_factory = sqlite3.Row
    connection.executescript(SCHEMA)
    connection.commit()
    return connection


def _seed_clients(connection: sqlite3.Connection, rng: random.Random, clients: int) -> None:
    rows = [
        (
            f"CL{index:04d}",
            f"ООО «Клиент-{index:03d}»",
            f"77{rng.randint(10_000_000, 99_999_999)}{index % 10}",
            SEGMENTS[index % len(SEGMENTS)],
            f"MGR{rng.randint(1, 12):02d}",
        )
        for index in range(clients)
    ]
    connection.executemany("INSERT INTO client (client_id, name, inn, segment, manager) VALUES (?, ?, ?, ?, ?)", rows)


def _seed_accounts(connection: sqlite3.Connection, rng: random.Random, clients: int) -> list[tuple[str, float]]:
    accounts: list[tuple[str, float]] = []
    for index in range(clients):
        for account_index in range(rng.randint(1, 3)):
            account_id = f"AC{index:04d}{account_index}"
            connection.execute(
                "INSERT INTO account (account_id, client_id, currency, opened_dt) VALUES (?, ?, ?, ?)",
                (account_id, f"CL{index:04d}", CURRENCIES[rng.randrange(len(CURRENCIES))],
                 f"2025-{rng.randint(1, 12):02d}-{rng.randint(1, 28):02d}"),
            )
            accounts.append((account_id, rng.uniform(50_000, 3_000_000)))
    return accounts


def _seed_turnover(connection: sqlite3.Connection, rng: random.Random, accounts: list[tuple[str, float]], days: int) -> None:
    rows: list[tuple] = []
    for account_id, start in accounts:
        balance = start
        for day in range(days):
            balance = max(0.0, balance + rng.gauss(0, 25_000))
            rows.append((account_id, f"2026-10-{day + 1:02d}", round(rng.uniform(0, 200_000), 2),
                         round(rng.uniform(0, 200_000), 2), round(balance, 2)))
    connection.executemany("INSERT INTO turnover (account_id, dt, debit, credit, balance) VALUES (?, ?, ?, ?, ?)", rows)


def _seed_loans(connection: sqlite3.Connection, rng: random.Random, clients: int) -> None:
    rows: list[tuple] = []
    for index in range(clients):
        for product in rng.sample(PRODUCTS, k=rng.randint(1, 2)):
            limit_amt = round(rng.uniform(500_000, 40_000_000), 2)
            rows.append((f"CL{index:04d}", product, limit_amt, round(limit_amt * rng.uniform(0.05, 0.9), 2),
                         round(rng.choice([0.0, 0.0, 0.0, 1200.0]), 2)))
    connection.executemany("INSERT INTO loan (client_id, product, limit_amt, debt_amt, overdue_amt) VALUES (?, ?, ?, ?, ?)", rows)


def _seed_checks(connection: sqlite3.Connection) -> None:
    checks = [
        ("turnover.freshness", "PASS", 2.1),
        ("turnover.duplicate_grain", "PASS", 0.0),
        ("turnover.orphan_client", "FAIL", 6.0),
        ("loans.debt_within_limit", "WARN", 3.0),
    ]
    connection.executemany(
        "INSERT INTO dq_run (run_id, check_id, status, metric, run_ts) VALUES (?, ?, ?, ?, ?)",
        [(f"RUN-{check_id}", check_id, status, metric, "2026-11-06T06:00:00") for check_id, status, metric in checks],
    )


def seed(connection: sqlite3.Connection, clients: int = 120, days: int = 40, seed_value: int = 17) -> dict[str, int]:
    """Детерминированное наполнение: один и тот же seed даёт одни и те же строки."""
    rng = random.Random(seed_value)
    for table in ("turnover", "account", "client", "loan", "dq_run"):
        connection.execute(f"DELETE FROM {table}")

    _seed_clients(connection, rng, clients)
    accounts = _seed_accounts(connection, rng, clients)
    _seed_turnover(connection, rng, accounts, days)
    _seed_loans(connection, rng, clients)
    _seed_checks(connection)
    connection.commit()
    return table_counts(connection)


def table_counts(connection: sqlite3.Connection) -> dict[str, int]:
    names = [row["name"] for row in connection.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
    )]
    return {name: int(connection.execute(f"SELECT COUNT(*) FROM {name}").fetchone()[0]) for name in names}


def tables(connection: sqlite3.Connection) -> list[dict]:
    result: list[dict] = []
    for name in table_counts(connection):
        columns = [
            {"name": row["name"], "type": row["type"], "pk": bool(row["pk"])}
            for row in connection.execute(f"PRAGMA table_info({name})")
        ]
        rows = int(connection.execute(f"SELECT COUNT(*) FROM {name}").fetchone()[0])
        result.append({"table": name, "columns": columns, "rows": rows})
    return result


def preview(connection: sqlite3.Connection, table: str, limit: int = 20) -> dict:
    if table not in table_counts(connection):
        raise KeyError(f"нет таблицы {table}")
    cursor = connection.execute(f"SELECT * FROM {table} LIMIT ?", (limit,))
    columns = [description[0] for description in cursor.description or ()]
    rows = [dict(row) for row in cursor.fetchall()]
    return {"table": table, "columns": columns, "rows": rows, "limit": limit}

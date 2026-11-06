"""Хранилище демо-данных для API: sqlite-схема и запросы."""

from __future__ import annotations

import random
import sqlite3
from datetime import date, timedelta

SCHEMA = """
CREATE TABLE IF NOT EXISTS client (
    client_id TEXT PRIMARY KEY,
    name      TEXT NOT NULL,
    segment   TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS turnover (
    client_id TEXT NOT NULL,
    dt        TEXT NOT NULL,
    debit     REAL NOT NULL,
    credit    REAL NOT NULL,
    balance   REAL NOT NULL,
    PRIMARY KEY (client_id, dt)
);
CREATE TABLE IF NOT EXISTS portfolio (
    client_id     TEXT NOT NULL,
    product       TEXT NOT NULL,
    limit_amt     REAL NOT NULL,
    debt_amt      REAL NOT NULL,
    interest_rate REAL NOT NULL,
    PRIMARY KEY (client_id, product)
);
CREATE TABLE IF NOT EXISTS dq_status (
    check_id     TEXT PRIMARY KEY,
    status       TEXT NOT NULL,
    metric_value REAL,
    checked_at   TEXT NOT NULL
);
"""

SEGMENTS = ["МСБ", "Средний бизнес", "Крупный бизнес"]
PRODUCTS = ["Овердрафт", "Кредитная линия", "Торговое финансирование"]


def connect(path: str) -> sqlite3.Connection:
    connection = sqlite3.connect(path, check_same_thread=False)
    connection.row_factory = sqlite3.Row
    connection.executescript(SCHEMA)
    connection.commit()
    return connection


def seed(connection: sqlite3.Connection, seed_value: int = 7, clients: int = 25, days: int = 30) -> None:
    rng = random.Random(seed_value)
    today = date(2026, 11, 6)
    for index in range(clients):
        client_id = f"CL{index:03d}"
        connection.execute(
            "INSERT OR REPLACE INTO client (client_id, name, segment) VALUES (?, ?, ?)",
            (client_id, f"ООО «Клиент-{index}»", rng.choice(SEGMENTS)),
        )
        balance = rng.uniform(100_000, 5_000_000)
        for offset in range(days, 0, -1):
            day = (today - timedelta(days=offset)).isoformat()
            debit = round(rng.uniform(0, 900_000), 2)
            credit = round(rng.uniform(0, 900_000), 2)
            balance = max(0.0, balance + credit - debit)
            connection.execute(
                "INSERT OR REPLACE INTO turnover (client_id, dt, debit, credit, balance)"
                " VALUES (?, ?, ?, ?, ?)",
                (client_id, day, debit, credit, round(balance, 2)),
            )
        for product in rng.sample(PRODUCTS, k=rng.randint(1, len(PRODUCTS))):
            limit_amt = round(rng.uniform(1_000_000, 50_000_000), 2)
            connection.execute(
                "INSERT OR REPLACE INTO portfolio (client_id, product, limit_amt, debt_amt, interest_rate)"
                " VALUES (?, ?, ?, ?, ?)",
                (
                    client_id,
                    product,
                    limit_amt,
                    round(limit_amt * rng.uniform(0.1, 0.9), 2),
                    round(rng.uniform(9.5, 24.0), 2),
                ),
            )
    for check_id, status, value in [
        ("turnover.freshness", "PASS", 2.1),
        ("turnover.orphan_client", "FAIL", 6.0),
        ("client.segment.completeness", "WARN", 0.031),
    ]:
        connection.execute(
            "INSERT OR REPLACE INTO dq_status (check_id, status, metric_value, checked_at)"
            " VALUES (?, ?, ?, ?)",
            (check_id, status, value, today.isoformat()),
        )
    connection.commit()


def list_clients(connection: sqlite3.Connection, segment: str | None, limit: int, offset: int) -> dict:
    where = ""
    params: list = []
    if segment:
        where = " WHERE segment = ?"
        params.append(segment)
    total = connection.execute(f"SELECT COUNT(*) AS c FROM client{where}", params).fetchone()["c"]
    rows = connection.execute(
        f"SELECT client_id, name, segment FROM client{where} ORDER BY client_id LIMIT ? OFFSET ?",
        [*params, limit, offset],
    ).fetchall()
    return {
        "total": total,
        "limit": limit,
        "offset": offset,
        "items": [dict(row) for row in rows],
    }


def get_client(connection: sqlite3.Connection, client_id: str) -> dict | None:
    row = connection.execute(
        "SELECT client_id, name, segment FROM client WHERE client_id = ?", (client_id,)
    ).fetchone()
    return dict(row) if row else None


def turnover_for_client(
    connection: sqlite3.Connection, client_id: str, date_from: str, date_to: str
) -> dict:
    rows = connection.execute(
        "SELECT dt, debit, credit, balance FROM turnover"
        " WHERE client_id = ? AND dt BETWEEN ? AND ? ORDER BY dt",
        (client_id, date_from, date_to),
    ).fetchall()
    aggregate = connection.execute(
        "SELECT COUNT(*) AS days, SUM(debit) AS debit, SUM(credit) AS credit,"
        " ROUND(AVG(balance), 2) AS avg_balance FROM turnover"
        " WHERE client_id = ? AND dt BETWEEN ? AND ?",
        (client_id, date_from, date_to),
    ).fetchone()
    return {
        "client_id": client_id,
        "period": {"from": date_from, "to": date_to},
        "summary": {
            "days": aggregate["days"] or 0,
            "debit": round(aggregate["debit"] or 0.0, 2),
            "credit": round(aggregate["credit"] or 0.0, 2),
            "avg_balance": aggregate["avg_balance"] or 0.0,
        },
        "items": [dict(row) for row in rows],
    }


def portfolio_summary(connection: sqlite3.Connection, min_debt: float) -> dict:
    rows = connection.execute(
        "SELECT product, COUNT(DISTINCT client_id) AS clients, ROUND(SUM(debt_amt), 2) AS debt,"
        " ROUND(AVG(interest_rate), 2) AS avg_rate, ROUND(SUM(debt_amt) * 100.0 /"
        " NULLIF(SUM(limit_amt), 0), 2) AS utilisation_pct"
        " FROM portfolio WHERE debt_amt >= ? GROUP BY product ORDER BY debt DESC",
        (min_debt,),
    ).fetchall()
    return {"min_debt": min_debt, "items": [dict(row) for row in rows]}


def dq_status(connection: sqlite3.Connection) -> dict:
    rows = connection.execute(
        "SELECT check_id, status, metric_value, checked_at FROM dq_status ORDER BY check_id"
    ).fetchall()
    order = {"PASS": 0, "WARN": 1, "FAIL": 2, "ERROR": 3}
    overall = "PASS"
    for row in rows:
        if order.get(row["status"], 0) > order.get(overall, 0):
            overall = row["status"]
    return {"overall": overall, "checks": [dict(row) for row in rows]}

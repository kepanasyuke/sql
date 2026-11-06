"""Идемпотентная инкрементальная загрузка с водяным знаком.

Задача: повторный запуск загрузки не должен ни дублировать строки, ни терять
данные при падении на середине. Решение — upsert по бизнес-ключу + журнал
прогонов (`etl_run`) и водяной знак (`etl_watermark`), а источник передаётся
фабрикой, чтобы retry мог перечитать его заново.
"""

from __future__ import annotations

import sqlite3
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Sequence

RUNNING = "RUNNING"
SUCCESS = "SUCCESS"
FAILED = "FAILED"

LEDGER_DDL = """
CREATE TABLE IF NOT EXISTS etl_run (
    run_id         TEXT PRIMARY KEY,
    table_name     TEXT NOT NULL,
    started_at     TEXT NOT NULL,
    finished_at    TEXT,
    status         TEXT NOT NULL,
    attempts       INTEGER NOT NULL DEFAULT 0,
    rows_received  INTEGER NOT NULL DEFAULT 0,
    rows_written   INTEGER NOT NULL DEFAULT 0,
    rows_deduped   INTEGER NOT NULL DEFAULT 0,
    watermark_before TEXT,
    watermark_after  TEXT,
    error          TEXT
);
CREATE TABLE IF NOT EXISTS etl_watermark (
    table_name TEXT PRIMARY KEY,
    watermark  TEXT,
    updated_at TEXT NOT NULL
);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _sql_type(values: Iterable[Any]) -> str:
    first = next((value for value in values if value is not None), None)
    if isinstance(first, bool):
        detected = "INTEGER"
    elif isinstance(first, (int, float)):
        detected = "REAL"
    else:
        detected = "TEXT"
    return detected


@dataclass
class LoadResult:
    run_id: str
    table: str
    status: str
    attempts: int
    rows_received: int
    rows_written: int
    rows_deduped: int
    watermark_before: str | None
    watermark_after: str | None
    duration_ms: int
    error: str | None = None

    def as_dict(self) -> dict:
        return {
            "run_id": self.run_id,
            "table": self.table,
            "status": self.status,
            "attempts": self.attempts,
            "rows_received": self.rows_received,
            "rows_written": self.rows_written,
            "rows_deduped": self.rows_deduped,
            "watermark_before": self.watermark_before,
            "watermark_after": self.watermark_after,
            "duration_ms": self.duration_ms,
            "error": self.error,
        }


@dataclass
class Loader:
    """Загрузчик в SQLite-приёмник (прод-версия: тот же контракт для Greenplum)."""

    dsn: str
    table: str
    key: str
    watermark: str
    retries: int = 3
    backoff: float = 0.05
    columns: Sequence[str] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        path = Path(self.dsn)
        if self.dsn != ":memory:":
            path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(self.dsn)
        self._conn.row_factory = sqlite3.Row
        self._ensure_ledger()

    # -- инфраструктура ----------------------------------------------------
    def _ensure_ledger(self) -> None:
        self._conn.executescript(LEDGER_DDL)
        self._conn.commit()

    def ensure_table(self, rows: Sequence[Mapping]) -> None:
        exists = self._conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?", (self.table,)
        ).fetchone()
        if not exists and rows:
            columns = list(self.columns) or list(rows[0].keys())
            definitions = [f"{self.key} TEXT NOT NULL", f"{self.watermark} TEXT NOT NULL"]
            for name in columns:
                if name not in (self.key, self.watermark):
                    definitions.append(f"{name} {_sql_type(row.get(name) for row in rows[:50])}")
            definitions.append(f"PRIMARY KEY ({self.key}, {self.watermark})")
            self._conn.execute(f"CREATE TABLE {self.table} ({', '.join(definitions)})")
            self._conn.commit()

    def current_watermark(self) -> str | None:
        row = self._conn.execute(
            "SELECT watermark FROM etl_watermark WHERE table_name = ?", (self.table,)
        ).fetchone()
        return row["watermark"] if row else None

    # -- загрузка ----------------------------------------------------------
    def load(
        self,
        source: Callable[[], Iterable[Mapping]],
        dry_run: bool = False,
    ) -> LoadResult:
        started = time.perf_counter()
        run_id = f"{self.table}-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%f')}"
        watermark_before = self.current_watermark()
        self._conn.execute(
            "INSERT INTO etl_run (run_id, table_name, started_at, status, watermark_before)"
            " VALUES (?, ?, ?, ?, ?)",
            (run_id, self.table, _now(), RUNNING, watermark_before),
        )
        self._conn.commit()

        attempts = 0
        last_error: str | None = None
        rows: list[dict] = []
        max_attempts = max(self.retries, 1)
        loaded = False
        while attempts < max_attempts and not loaded:
            attempts += 1
            try:
                rows = [dict(row) for row in source()]
                loaded = True
            except Exception as exc:  # источник может падать транзиентно
                last_error = f"{type(exc).__name__}: {exc}"
                if attempts < max_attempts:
                    time.sleep(self.backoff * attempts)

        if len(rows) == 0 and last_error:
            self._finish(run_id, FAILED, attempts, 0, 0, 0, watermark_before, last_error)
            raise RuntimeError(f"загрузка не удалась после {attempts} попыток: {last_error}")

        self.ensure_table(rows)
        unique: dict[tuple, dict] = {}
        for row in rows:
            unique[(row[self.key], row[self.watermark])] = row
        deduped = len(rows) - len(unique)

        written = 0
        after = watermark_before
        if not dry_run and unique:
            present = {
                column[1]
                for column in self._conn.execute(f"PRAGMA table_info({self.table})").fetchall()
            }
            names = [name for name in unique[next(iter(unique))] if name in present]
            placeholders = ", ".join("?" for _ in names)
            updates = ", ".join(f"{name} = excluded.{name}" for name in names if name not in (self.key, self.watermark))
            statement = (
                f"INSERT INTO {self.table} ({', '.join(names)}) VALUES ({placeholders})"
                f" ON CONFLICT({self.key}, {self.watermark}) DO UPDATE SET {updates or f'{self.key} = excluded.{self.key}'}"
            )
            self._conn.executemany(statement, [tuple(row.get(name) for name in names) for row in unique.values()])
            written = len(unique)
            candidate = max(row[self.watermark] for row in unique.values())
            after = max(filter(None, [watermark_before, candidate]))
            self._conn.execute(
                "INSERT INTO etl_watermark (table_name, watermark, updated_at) VALUES (?, ?, ?)"
                " ON CONFLICT(table_name) DO UPDATE SET watermark = excluded.watermark,"
                " updated_at = excluded.updated_at",
                (self.table, after, _now()),
            )
            self._conn.commit()

        result = LoadResult(
            run_id=run_id,
            table=self.table,
            status=SUCCESS,
            attempts=attempts,
            rows_received=len(rows),
            rows_written=written,
            rows_deduped=deduped,
            watermark_before=watermark_before,
            watermark_after=after if not dry_run else watermark_before,
            duration_ms=int((time.perf_counter() - started) * 1000),
        )
        self._finish(run_id, SUCCESS, attempts, len(rows), written, deduped, result.watermark_after)
        return result

    def _finish(
        self,
        run_id: str,
        status: str,
        attempts: int,
        received: int,
        written: int,
        deduped: int,
        after: str | None,
        error: str | None = None,
    ) -> None:
        self._conn.execute(
            "UPDATE etl_run SET finished_at = ?, status = ?, attempts = ?, rows_received = ?,"
            " rows_written = ?, rows_deduped = ?, watermark_after = ?, error = ?"
            " WHERE run_id = ?",
            (_now(), status, attempts, received, written, deduped, after, error, run_id),
        )
        self._conn.commit()

    def backfill(
        self,
        days: Sequence[str],
        source_for_day: Callable[[str], Iterable[Mapping]],
    ) -> list[LoadResult]:
        """Перезаливка по дням: каждый день — отдельный прогон со своим журналом."""
        results = []
        for day in days:
            results.append(self.load(lambda day=day: source_for_day(day)))
        return results

    def last_runs(self, limit: int = 10) -> list[dict]:
        rows = self._conn.execute(
            "SELECT * FROM etl_run WHERE table_name = ? ORDER BY started_at DESC LIMIT ?",
            (self.table, limit),
        ).fetchall()
        return [dict(row) for row in rows]

    def row_count(self) -> int:
        return int(self._conn.execute(f"SELECT COUNT(*) AS c FROM {self.table}").fetchone()["c"])

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> "Loader":
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

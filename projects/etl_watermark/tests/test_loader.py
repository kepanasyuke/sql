"""Тесты идемпотентности, retry, водяного знака и backfill."""

from __future__ import annotations

from pathlib import Path

import pytest

from projects.etl_watermark.loader import FAILED, SUCCESS, Loader

ROWS = [
    {"client_id": "CL001", "dt": "2026-11-01", "amount": 100.0, "segment": "МСБ"},
    {"client_id": "CL002", "dt": "2026-11-01", "amount": 250.5, "segment": "Крупный бизнес"},
]


def _loader(tmp_path: Path, **kwargs) -> Loader:
    return Loader(
        dsn=str(tmp_path / "etl.sqlite3"),
        table="client_daily_amount",
        key="client_id",
        watermark="dt",
        **kwargs,
    )


def test_load_writes_rows_and_advances_watermark(tmp_path: Path) -> None:
    with _loader(tmp_path) as loader:
        result = loader.load(lambda: ROWS)
        assert result.status == SUCCESS
        assert result.rows_written == 2
        assert loader.row_count() == 2
        assert loader.current_watermark() == "2026-11-01"


def test_second_load_is_idempotent(tmp_path: Path) -> None:
    with _loader(tmp_path) as loader:
        loader.load(lambda: ROWS)
        second = loader.load(lambda: ROWS)
        assert second.rows_written == 2          # upsert перезаписал те же ключи
        assert loader.row_count() == 2           # но строк не прибавилось
        assert len(loader.last_runs()) == 2


def test_changed_values_are_updated_not_duplicated(tmp_path: Path) -> None:
    with _loader(tmp_path) as loader:
        loader.load(lambda: ROWS)
        updated = [dict(ROWS[0], amount=999.0), ROWS[1]]
        loader.load(lambda: updated)
        amount = loader._conn.execute(
            "SELECT amount FROM client_daily_amount WHERE client_id = 'CL001'"
        ).fetchone()["amount"]
        assert amount == 999.0
        assert loader.row_count() == 2


def test_duplicates_inside_batch_are_collapsed(tmp_path: Path) -> None:
    with _loader(tmp_path) as loader:
        result = loader.load(lambda: ROWS + [dict(ROWS[0], amount=777.0)])
        assert result.rows_received == 3
        assert result.rows_deduped == 1
        assert result.rows_written == 2


def test_transient_source_failure_is_retried(tmp_path: Path) -> None:
    state = {"attempts": 0}

    def flaky():
        state["attempts"] += 1
        if state["attempts"] < 2:
            raise ConnectionError("источник недоступен")
        return ROWS

    with _loader(tmp_path, retries=3, backoff=0.0) as loader:
        result = loader.load(flaky)
        assert result.status == SUCCESS
        assert result.attempts == 2
        assert result.rows_written == 2


def test_permanent_failure_is_recorded(tmp_path: Path) -> None:
    def broken():
        raise TimeoutError("источник молчит")

    with _loader(tmp_path, retries=2, backoff=0.0) as loader:
        with pytest.raises(RuntimeError):
            loader.load(broken)
        runs = loader.last_runs()
        assert runs[0]["status"] == FAILED
        assert "TimeoutError" in runs[0]["error"]
        assert runs[0]["attempts"] == 2


def test_dry_run_writes_nothing(tmp_path: Path) -> None:
    with _loader(tmp_path) as loader:
        result = loader.load(lambda: ROWS, dry_run=True)
        assert result.rows_written == 0
        assert result.rows_received == 2
        assert loader.current_watermark() is None


def test_backfill_covers_all_days_and_keeps_max_watermark(tmp_path: Path) -> None:
    days = ["2026-11-01", "2026-11-02", "2026-11-03"]

    def source_for_day(day: str):
        return [
            {"client_id": f"CL{index:03d}", "dt": day, "amount": float(index), "segment": "МСБ"}
            for index in range(5)
        ]

    with _loader(tmp_path) as loader:
        results = loader.backfill(days, source_for_day)
        assert len(results) == 3
        assert loader.row_count() == 15
        assert loader.current_watermark() == "2026-11-03"

        # повторный backfill старого дня не откатывает водяной знак назад
        loader.load(lambda: source_for_day("2026-11-01"))
        assert loader.current_watermark() == "2026-11-03"

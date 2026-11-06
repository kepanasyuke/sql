"""Тесты миграций: up/down, идемпотентность, дрейф, атомарность, блокировка."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from projects.sql_migrations.migrator import (
    MigrationError,
    Migrator,
    discover,
    parse_migration,
)

PROJECT_MIGRATIONS = Path(__file__).resolve().parents[1] / "migrations"


@pytest.fixture()
def workdir(tmp_path: Path) -> Path:
    target = tmp_path / "migrations"
    shutil.copytree(PROJECT_MIGRATIONS, target)
    return target


def _tables(dsn: str) -> set[str]:
    import sqlite3

    with sqlite3.connect(dsn) as connection:
        rows = connection.execute("SELECT name FROM sqlite_master WHERE type IN ('table','view')").fetchall()
    return {row[0] for row in rows}


def test_discover_orders_and_parses_sections(workdir: Path) -> None:
    migrations = discover(workdir)
    assert [item.version for item in migrations] == [1, 2, 3]
    assert "CREATE TABLE dim_client" in migrations[0].up
    assert "DROP TABLE dim_client" in migrations[0].down


def test_bad_filename_and_duplicates(workdir: Path) -> None:
    (workdir / "bad_name.sql").write_text("SELECT 1;", encoding="utf-8")
    with pytest.raises(MigrationError, match="NNNN_name.sql"):
        discover(workdir)

    (workdir / "bad_name.sql").unlink()
    shutil.copyfile(workdir / "0001_create_clients.sql", workdir / "0001_duplicate.sql")
    with pytest.raises(MigrationError, match="дубликаты версий"):
        discover(workdir)


def test_parse_migration_checksum_changes_with_content(tmp_path: Path) -> None:
    path = tmp_path / "0009_demo.sql"
    path.write_text("-- +migrate up\nCREATE TABLE t (id INT);\n", encoding="utf-8")
    first = parse_migration(path)
    path.write_text("-- +migrate up\nCREATE TABLE t (id INT, extra INT);\n", encoding="utf-8")
    second = parse_migration(path)
    assert first.checksum != second.checksum


def test_apply_is_idempotent(workdir: Path, tmp_path: Path) -> None:
    dsn = str(tmp_path / "db.sqlite3")
    with Migrator(dsn, workdir) as migrator:
        applied = migrator.apply()
        assert [item["version"] for item in applied] == [1, 2, 3]
        assert migrator.apply() == []
        states = {row["version"]: row["state"] for row in migrator.status()}
    assert states == {1: "APPLIED", 2: "APPLIED", 3: "APPLIED"}
    assert {"dim_client", "v_active_clients", "schema_migrations"} <= _tables(dsn)


def test_apply_to_version(workdir: Path, tmp_path: Path) -> None:
    dsn = str(tmp_path / "db.sqlite3")
    with Migrator(dsn, workdir) as migrator:
        migrator.apply(target_version=2)
        states = {row["version"]: row["state"] for row in migrator.status()}
    assert states == {1: "APPLIED", 2: "APPLIED", 3: "PENDING"}
    assert "v_active_clients" not in _tables(dsn)


def test_drift_is_detected_and_blocks_apply(workdir: Path, tmp_path: Path) -> None:
    dsn = str(tmp_path / "db.sqlite3")
    with Migrator(dsn, workdir) as migrator:
        migrator.apply()
        target = workdir / "0002_add_segment.sql"
        target.write_text(target.read_text(encoding="utf-8") + "\n-- правка\n", encoding="utf-8")
        drift = migrator.drift()
        assert [item["version"] for item in drift] == [2]
        with pytest.raises(MigrationError, match="дрейф"):
            migrator.apply()


def test_rollback_reverts_schema(workdir: Path, tmp_path: Path) -> None:
    dsn = str(tmp_path / "db.sqlite3")
    with Migrator(dsn, workdir) as migrator:
        migrator.apply()
        rolled = migrator.rollback()
        assert rolled[0]["version"] == 3
        assert "v_active_clients" not in _tables(dsn)

        migrator.rollback(version=2)
        states = {row["version"]: row["state"] for row in migrator.status()}
    assert states[2] == "PENDING"
    assert states[1] == "APPLIED"


def test_failed_migration_leaves_no_trace(tmp_path: Path) -> None:
    directory = tmp_path / "migrations"
    directory.mkdir()
    (directory / "0001_ok.sql").write_text(
        "-- +migrate up\nCREATE TABLE ok (id INT);\n-- +migrate down\nDROP TABLE ok;\n", encoding="utf-8"
    )
    (directory / "0002_broken.sql").write_text(
        "-- +migrate up\nCREATE TABLE half (id INT);\nSELEC broken syntax;\n", encoding="utf-8"
    )
    dsn = str(tmp_path / "db.sqlite3")
    with Migrator(dsn, directory) as migrator:
        with pytest.raises(MigrationError, match="ошибка применения"):
            migrator.apply()
        applied = migrator.applied()
    assert list(applied) == [1]
    assert "half" not in _tables(dsn)          # частичные изменения откатились


def test_lock_prevents_concurrent_apply(workdir: Path, tmp_path: Path) -> None:
    dsn = str(tmp_path / "db.sqlite3")
    with Migrator(dsn, workdir) as first:
        first.lock()
        with Migrator(dsn, workdir) as second:
            with pytest.raises(MigrationError, match="другой процесс"):
                second.apply()
        first.unlock()
        assert [item["version"] for item in first.apply()] == [1, 2, 3]

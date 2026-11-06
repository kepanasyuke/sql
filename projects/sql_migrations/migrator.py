"""Миграции с контролем дрейфа и атомарным применением.

Отличия от «просто выполнить SQL-файлы»:
  * каждая миграция применяется в транзакции вместе с записью в журнал —
    либо и схема, либо ничего;
  * журнал хранит контрольную сумму файла: если уже применённую миграцию
    отредактировали, `drift()` это покажет (иначе состояние схемы в прод и в
    репозитории расходится незаметно);
  * секции `-- +migrate up` / `-- +migrate down` позволяют откатиться;
  * блокировка в БД не даёт двум процессам применить миграции одновременно.
"""

from __future__ import annotations

import hashlib
import re
import sqlite3
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

SECTION_RE = re.compile(r"^\s*--\s*\+migrate\s+(up|down)\b.*$", re.IGNORECASE | re.MULTILINE)
VERSION_RE = re.compile(r"^(\d+)[_-](.+)\.sql$")

LEDGER_DDL = """
CREATE TABLE IF NOT EXISTS schema_migrations (
    version     INTEGER PRIMARY KEY,
    name        TEXT NOT NULL,
    checksum    TEXT NOT NULL,
    applied_at  TEXT NOT NULL,
    duration_ms INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS schema_lock (
    id        INTEGER PRIMARY KEY CHECK (id = 1),
    locked_at TEXT NOT NULL,
    owner     TEXT NOT NULL
);
"""


class MigrationError(RuntimeError):
    """Миграция невозможна: дрейф, блокировка, ошибка SQL."""


@dataclass(frozen=True)
class Migration:
    version: int
    name: str
    path: Path
    up: str
    down: str
    checksum: str

    @property
    def key(self) -> str:
        return f"{self.version:04d}_{self.name}"


def parse_migration(path: Path) -> Migration:
    match = VERSION_RE.match(path.name)
    if not match:
        raise MigrationError(f"имя файла не соответствует NNNN_name.sql: {path.name}")
    text = path.read_text(encoding="utf-8")
    sections: dict[str, list[str]] = {"up": [], "down": []}
    current = "up"
    for line in text.splitlines():
        marker = SECTION_RE.match(line)
        if marker:
            current = marker.group(1).lower()
        else:
            sections[current].append(line)
    return Migration(
        version=int(match.group(1)),
        name=match.group(2),
        path=path,
        up="\n".join(sections["up"]).strip(),
        down="\n".join(sections["down"]).strip(),
        checksum=hashlib.sha256(path.read_bytes()).hexdigest()[:16],
    )


def discover(directory: str | Path) -> list[Migration]:
    folder = Path(directory)
    if not folder.exists():
        raise MigrationError(f"нет каталога миграций: {folder}")
    migrations = [parse_migration(path) for path in folder.glob("*.sql")]
    migrations.sort(key=lambda item: item.version)
    versions = [item.version for item in migrations]
    duplicates = {version for version in versions if versions.count(version) > 1}
    if duplicates:
        raise MigrationError(f"дубликаты версий: {sorted(duplicates)}")
    return migrations


def _literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def split_statements(sql: str) -> list[str]:
    """Разбивает скрипт на утверждения.

    `executescript` не годится: в legacy-режиме он делает неявный COMMIT перед
    запуском скрипта и ломает атомарность миграции. Поэтому утверждения
    выполняются по одному внутри явной транзакции.
    """
    cleaned = re.sub(r"(?m)^\s*--.*$", "", sql)
    statements: list[str] = []
    buffer: list[str] = []
    quote: str | None = None
    for char in cleaned:
        if quote:
            buffer.append(char)
            if char == quote:
                quote = None
        elif char in ("'", '"'):
            quote = char
            buffer.append(char)
        elif char == ";":
            statement = "".join(buffer).strip()
            if statement:
                statements.append(statement)
            buffer = []
        else:
            buffer.append(char)
    tail = "".join(buffer).strip()
    if tail:
        statements.append(tail)
    return statements


class Migrator:
    def __init__(self, dsn: str, directory: str | Path, owner: str = "local"):
        self.dsn = dsn
        self.directory = Path(directory)
        self.owner = owner
        if dsn != ":memory:":
            Path(dsn).parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(dsn, isolation_level=None)
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(LEDGER_DDL)
        self._conn.commit()

    # -- состояние ---------------------------------------------------------
    def applied(self) -> dict[int, dict]:
        rows = self._conn.execute("SELECT * FROM schema_migrations ORDER BY version").fetchall()
        return {row["version"]: dict(row) for row in rows}

    def status(self) -> list[dict]:
        applied = self.applied()
        result = []
        for migration in discover(self.directory):
            record = applied.get(migration.version)
            if record is None:
                state = "PENDING"
            elif record["checksum"] != migration.checksum:
                state = "DRIFT"
            else:
                state = "APPLIED"
            result.append(
                {
                    "version": migration.version,
                    "name": migration.name,
                    "state": state,
                    "checksum": migration.checksum,
                    "applied_at": record["applied_at"] if record else None,
                }
            )
        return result

    def drift(self) -> list[dict]:
        return [item for item in self.status() if item["state"] == "DRIFT"]

    def pending(self) -> list[Migration]:
        applied = self.applied()
        return [item for item in discover(self.directory) if item.version not in applied]

    # -- блокировка --------------------------------------------------------
    def lock(self) -> None:
        try:
            self._conn.execute(
                "INSERT INTO schema_lock (id, locked_at, owner) VALUES (1, ?, ?)",
                (datetime.now(timezone.utc).isoformat(timespec="seconds"), self.owner),
            )
            self._conn.commit()
        except sqlite3.IntegrityError as exc:
            raise MigrationError("миграции уже применяет другой процесс (schema_lock)") from exc

    def unlock(self) -> None:
        self._conn.execute("DELETE FROM schema_lock WHERE id = 1")
        self._conn.commit()

    # -- применение --------------------------------------------------------
    def apply(self, target_version: int | None = None) -> list[dict]:
        drifted = self.drift()
        if drifted:
            names = ", ".join(item["name"] for item in drifted)
            raise MigrationError(f"дрейф применённых миграций: {names}")

        self.lock()
        applied: list[dict] = []
        try:
            for migration in (
                item
                for item in self.pending()
                if target_version is None or item.version <= target_version
            ):
                started = time.perf_counter()
                self._run_in_transaction(
                    migration.up,
                    ledger_insert=self._insert_sql(migration, int((time.perf_counter() - started) * 1000)),
                )
                applied.append({"version": migration.version, "name": migration.name})
        finally:
            self.unlock()
        return applied

    def rollback(self, version: int | None = None) -> list[dict]:
        applied = self.applied()
        rolled: list[dict] = []
        if applied:
            target = version if version is not None else max(applied)
            migrations = {item.version: item for item in discover(self.directory)}
            if target not in applied:
                raise MigrationError(f"миграция {target} не применена")
            migration = migrations.get(target)
            if migration is None:
                raise MigrationError(f"файл миграции {target} не найден в каталоге")
            if not migration.down.strip():
                raise MigrationError(f"у миграции {migration.key} нет секции down")

            self.lock()
            try:
                self._run_in_transaction(
                    migration.down,
                    ledger_insert=f"DELETE FROM schema_migrations WHERE version = {migration.version};",
                )
            finally:
                self.unlock()
            rolled.append({"version": migration.version, "name": migration.name, "direction": "down"})
        return rolled

    def _insert_sql(self, migration: Migration, duration_ms: int) -> str:
        return (
            "INSERT INTO schema_migrations (version, name, checksum, applied_at, duration_ms) VALUES ("
            f"{migration.version}, {_literal(migration.name)}, {_literal(migration.checksum)}, "
            f"{_literal(datetime.now(timezone.utc).isoformat(timespec='seconds'))}, {duration_ms});"
        )

    def _run_in_transaction(self, sql: str, ledger_insert: str) -> None:
        """Схема и запись в журнал — в одной транзакции: либо всё, либо ничего."""
        statements = split_statements(sql) + split_statements(ledger_insert)
        self._conn.execute("BEGIN IMMEDIATE")
        try:
            for statement in statements:
                self._conn.execute(statement)
            self._conn.execute("COMMIT")
        except sqlite3.Error as exc:
            self._conn.execute("ROLLBACK")
            raise MigrationError(f"ошибка применения миграции: {exc}") from exc

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> "Migrator":
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

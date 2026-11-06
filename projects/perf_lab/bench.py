"""Бенчмарк SQL-вариантов: одинаковая логика, разная стоимость.

Правило лаборатории: сначала доказываем, что варианты возвращают одно и то же,
и только потом сравниваем время. Иначе «ускорение» может оказаться ускорением
неправильного запроса — самая частая ошибка при оптимизации.
"""

from __future__ import annotations

import hashlib
import random
import sqlite3
import statistics
import time
from dataclasses import dataclass, field
from pathlib import Path

SCHEMA = """
DROP TABLE IF EXISTS turnover;
DROP TABLE IF EXISTS client;
CREATE TABLE client (
    client_id TEXT PRIMARY KEY,
    segment   TEXT NOT NULL
);
CREATE TABLE turnover (
    account_id TEXT NOT NULL,
    client_id  TEXT NOT NULL,
    dt         TEXT NOT NULL,
    balance    REAL NOT NULL
);
"""


@dataclass(frozen=True)
class Variant:
    name: str
    sql: str
    setup: tuple[str, ...] = ()
    teardown: tuple[str, ...] = ()
    note: str = ""


@dataclass
class Scenario:
    name: str
    description: str
    variants: list[Variant]


@dataclass
class VariantResult:
    scenario: str
    variant: str
    p50_ms: float
    p95_ms: float
    runs: int
    rows: int
    result_hash: str
    matches_baseline: bool = True
    speedup: float | None = None
    note: str = ""


@dataclass
class ScenarioResult:
    name: str
    description: str
    variants: list[VariantResult] = field(default_factory=list)

    @property
    def fastest(self) -> VariantResult | None:
        return min(self.variants, key=lambda item: item.p50_ms) if self.variants else None


def prepare_database(dsn: str, rows: int = 20_000, clients: int = 120, days: int = 60, seed: int = 4) -> None:
    rng = random.Random(seed)
    if dsn != ":memory:":
        Path(dsn).parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(dsn)
    connection.executescript(SCHEMA)
    segments = ["МСБ", "Крупный бизнес", "Средний бизнес"]
    connection.executemany(
        "INSERT INTO client (client_id, segment) VALUES (?, ?)",
        [(f"CL{index:04d}", segments[index % 3]) for index in range(clients)],
    )
    accounts = max(1, rows // days)
    payload = []
    for account in range(accounts):
        client_id = f"CL{account % clients:04d}"
        for day in range(days):
            payload.append(
                (
                    f"AC{account:05d}",
                    client_id,
                    f"2026-{(day % 12) + 1:02d}-{(day % 28) + 1:02d}",
                    round(rng.uniform(0, 5_000_000), 2),
                )
            )
    connection.executemany("INSERT INTO turnover (account_id, client_id, dt, balance) VALUES (?, ?, ?, ?)", payload)
    connection.commit()
    connection.close()


def _hash_rows(rows: list[tuple]) -> str:
    digest = hashlib.sha256()
    for row in rows:
        digest.update(repr(row).encode("utf-8"))
    return digest.hexdigest()[:16]


def _percentile(values: list[float], q: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    position = (len(ordered) - 1) * q
    low = int(position)
    high = min(low + 1, len(ordered) - 1)
    return round(ordered[low] + (ordered[high] - ordered[low]) * (position - low), 3)


def measure(connection: sqlite3.Connection, variant: Variant, repeats: int = 5) -> tuple[list[tuple], list[float]]:
    for statement in variant.setup:
        connection.execute(statement)
    durations: list[float] = []
    rows: list[tuple] = []
    try:
        connection.execute(variant.sql).fetchall()          # прогрев: кэш страниц и плана
        for _ in range(repeats):
            started = time.perf_counter()
            rows = connection.execute(variant.sql).fetchall()
            durations.append((time.perf_counter() - started) * 1000)
    finally:
        for statement in variant.teardown:
            connection.execute(statement)
    return rows, durations


def run_scenario(connection: sqlite3.Connection, scenario: Scenario, repeats: int = 5) -> ScenarioResult:
    result = ScenarioResult(name=scenario.name, description=scenario.description)
    baseline_hash: str | None = None
    baseline_p50: float | None = None
    for variant in scenario.variants:
        rows, durations = measure(connection, variant, repeats)
        result_hash = _hash_rows(rows)
        if baseline_hash is None:
            baseline_hash, baseline_p50 = result_hash, _percentile(durations, 0.5)
        p50 = _percentile(durations, 0.5)
        result.variants.append(
            VariantResult(
                scenario=scenario.name,
                variant=variant.name,
                p50_ms=p50,
                p95_ms=_percentile(durations, 0.95),
                runs=len(durations),
                rows=len(rows),
                result_hash=result_hash,
                matches_baseline=result_hash == baseline_hash,
                speedup=round(baseline_p50 / p50, 2) if p50 > 0 else None,
                note=variant.note,
            )
        )
    return result


def default_scenarios() -> list[Scenario]:
    return [
        Scenario(
            name="last_balance_per_account",
            description="Последний остаток по счёту: коррелированный подзапрос против окна",
            variants=[
                Variant(
                    name="correlated_subquery",
                    sql="""
                        SELECT t.account_id,
                               (SELECT t2.balance FROM turnover t2
                                 WHERE t2.account_id = t.account_id
                                 ORDER BY t2.dt DESC LIMIT 1) AS balance
                          FROM (SELECT DISTINCT account_id FROM turnover) t
                         ORDER BY t.account_id
                    """,
                    note="по одному индексному поиску на счёт",
                ),
                Variant(
                    name="window_function",
                    sql="""
                        WITH ranked AS (
                            SELECT account_id, balance,
                                   ROW_NUMBER() OVER (PARTITION BY account_id ORDER BY dt DESC) AS rn
                              FROM turnover
                        )
                        SELECT account_id, balance FROM ranked WHERE rn = 1
                         ORDER BY account_id
                    """,
                    note="один проход + сортировка",
                ),
            ],
        ),
        Scenario(
            name="inactive_clients",
            description="Клиенты без оборотов за последние 7 дней: три способа анти-джойна",
            variants=[
                Variant(
                    name="not_in",
                    sql="""
                        SELECT client_id FROM client
                         WHERE client_id NOT IN (SELECT client_id FROM turnover WHERE dt >= '2026-03-01')
                         ORDER BY client_id
                    """,
                    note="NOT IN чувствителен к NULL в подзапросе",
                ),
                Variant(
                    name="not_exists",
                    sql="""
                        SELECT c.client_id FROM client c
                         WHERE NOT EXISTS (SELECT 1 FROM turnover t
                                            WHERE t.client_id = c.client_id AND t.dt >= '2026-03-01')
                         ORDER BY c.client_id
                    """,
                    note="анти-джойн, обычно лучший вариант",
                ),
                Variant(
                    name="left_join_is_null",
                    sql="""
                        SELECT c.client_id FROM client c
                          LEFT JOIN turnover t ON t.client_id = c.client_id AND t.dt >= '2026-03-01'
                         WHERE t.client_id IS NULL
                         ORDER BY c.client_id
                    """,
                    note="читается проще, но требует DISTINCT при дублях",
                ),
            ],
        ),
        Scenario(
            name="top_clients_range",
            description="Топ клиентов по оборотам за период: с индексом и без",
            variants=[
                Variant(
                    name="seq_scan",
                    sql="""
                        SELECT client_id, ROUND(SUM(balance), 2) AS total
                          FROM turnover WHERE dt BETWEEN '2026-02-01' AND '2026-03-15'
                         GROUP BY client_id ORDER BY total DESC LIMIT 10
                    """,
                    setup=("DROP INDEX IF EXISTS idx_turnover_client_dt",),
                    note="полный проход по таблице",
                ),
                Variant(
                    name="index_client_dt",
                    sql="""
                        SELECT client_id, ROUND(SUM(balance), 2) AS total
                          FROM turnover WHERE dt BETWEEN '2026-02-01' AND '2026-03-15'
                         GROUP BY client_id ORDER BY total DESC LIMIT 10
                    """,
                    setup=("CREATE INDEX IF NOT EXISTS idx_turnover_client_dt ON turnover (client_id, dt)",),
                    teardown=("DROP INDEX IF EXISTS idx_turnover_client_dt",),
                    note="covering index по ключу группировки",
                ),
            ],
        ),
    ]


def benchmark(dsn: str, repeats: int = 5, scenarios: list[Scenario] | None = None) -> list[ScenarioResult]:
    connection = sqlite3.connect(dsn)
    try:
        return [run_scenario(connection, scenario, repeats) for scenario in (scenarios or default_scenarios())]
    finally:
        connection.close()


def to_markdown(results: list[ScenarioResult]) -> str:
    lines = ["# Бенчмарк SQL-вариантов", ""]
    for result in results:
        fastest = result.fastest
        lines.append(f"## {result.name}")
        lines.append("")
        lines.append(result.description)
        lines.append("")
        lines.append("| Вариант | p50, мс | p95, мс | Строк | Ускорение | Результат совпал | Примечание |")
        lines.append("| --- | --- | --- | --- | --- | --- | --- |")
        for variant in result.variants:
            lines.append(
                f"| {variant.variant} | {variant.p50_ms} | {variant.p95_ms} | {variant.rows} | "
                f"{variant.speedup if variant.speedup is not None else '-'}x | "
                f"{'да' if variant.matches_baseline else 'НЕТ'} | {variant.note} |"
            )
        if fastest is not None:
            lines.append("")
            lines.append(f"Самый быстрый вариант: **{fastest.variant}** ({fastest.p50_ms} мс).")
        lines.append("")
    mismatched = [
        f"{variant.scenario}/{variant.variant}"
        for result in results
        for variant in result.variants
        if not variant.matches_baseline
    ]
    if mismatched:
        lines.append("**ВНИМАНИЕ: результаты вариантов расходятся:** " + ", ".join(mismatched))
    return "\n".join(lines) + "\n"


def statistics_note() -> str:
    return (
        "p50 устойчив к выбросам планировщика, p95 показывает худший типичный прогон; "
        "первый прогон каждого варианта — прогрев и в статистику не входит"
    )

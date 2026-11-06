"""Тесты perf-lab: подготовка данных, равенство результатов, отчёт."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from projects.perf_lab.bench import (
    Scenario,
    Variant,
    benchmark,
    default_scenarios,
    prepare_database,
    run_scenario,
    to_markdown,
)


@pytest.fixture()
def dsn(tmp_path: Path) -> str:
    path = str(tmp_path / "perf.sqlite3")
    prepare_database(path, rows=3000, clients=40, days=60)
    return path


def test_prepare_database_rows(dsn: str) -> None:
    with sqlite3.connect(dsn) as connection:
        turnover = connection.execute("SELECT COUNT(*) FROM turnover").fetchone()[0]
        clients = connection.execute("SELECT COUNT(*) FROM client").fetchone()[0]
    assert turnover == 3000
    assert clients == 40


def test_variants_return_same_result(dsn: str) -> None:
    scenario = default_scenarios()[0]
    connection = sqlite3.connect(dsn)
    try:
        result = run_scenario(connection, scenario, repeats=2)
    finally:
        connection.close()
    assert len(result.variants) == 2
    assert all(variant.matches_baseline for variant in result.variants)
    assert result.variants[0].result_hash == result.variants[1].result_hash
    assert all(variant.rows > 0 for variant in result.variants)
    assert all(variant.p50_ms >= 0 for variant in result.variants)


def test_index_variant_produces_same_rows(dsn: str) -> None:
    scenario = next(item for item in default_scenarios() if item.name == "top_clients_range")
    connection = sqlite3.connect(dsn)
    try:
        result = run_scenario(connection, scenario, repeats=2)
    finally:
        connection.close()
    scan, indexed = result.variants
    assert scan.rows == 10
    assert indexed.rows == 10
    assert indexed.matches_baseline is True
    assert indexed.speedup is not None


def test_result_mismatch_is_reported(dsn: str) -> None:
    bad = Scenario(
        name="wrong_variant",
        description="специально расходящиеся запросы",
        variants=[
            Variant("correct", "SELECT COUNT(*) FROM turnover"),
            Variant("typo", "SELECT COUNT(*) FROM turnover WHERE dt >= '2026-03-01'"),
        ],
    )
    connection = sqlite3.connect(dsn)
    try:
        result = run_scenario(connection, bad, repeats=1)
    finally:
        connection.close()
    assert result.variants[1].matches_baseline is False
    markdown = to_markdown([result])
    assert "результаты вариантов расходятся" in markdown


def test_markdown_report_structure(dsn: str) -> None:
    results = benchmark(dsn, repeats=2)
    markdown = to_markdown(results)
    assert markdown.startswith("# Бенчмарк SQL-вариантов")
    for scenario in results:
        assert f"## {scenario.name}" in markdown
        assert f"| {scenario.variants[0].variant} |" in markdown
    assert "Самый быстрый вариант" in markdown
    assert "p95" in markdown.splitlines()[2] or "p95" in markdown


def test_benchmark_runs_all_default_scenarios(dsn: str) -> None:
    results = benchmark(dsn, repeats=1)
    assert [item.name for item in results] == [
        "last_balance_per_account",
        "inactive_clients",
        "top_clients_range",
    ]
    assert all(len(item.variants) >= 2 for item in results)

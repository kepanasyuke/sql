"""Тесты профилировщика: типы, NULL, PII, подсказки проверок."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from projects.csv_profiler.cli import main
from projects.csv_profiler.profiler import (
    Profiler,
    detect_pii,
    infer_type,
    inn_valid,
    luhn_valid,
    mask,
)

ROWS = [
    {"client_id": "CL001", "inn": "7707083893", "amount": "1000", "segment": "МСБ", "opened": "2026-01-10"},
    {"client_id": "CL002", "inn": "7707083894", "amount": "2000.50", "segment": "", "opened": "2026-02-11"},
    {"client_id": "CL003", "inn": "500100732259", "amount": "3000", "segment": "Крупный бизнес", "opened": "2026-03-12"},
]


def _profiler() -> Profiler:
    profiler = Profiler()
    profiler.feed_rows(ROWS)
    return profiler


@pytest.mark.parametrize(
    "value, expected",
    [("42", "integer"), ("-7", "integer"), ("3.14", "number"), ("3,14", "number"),
     ("2026-01-10", "date"), ("2026-13-40", "text"), ("true", "boolean"), ("МСБ", "text")],
)
def test_infer_type(value: str, expected: str) -> None:
    assert infer_type(value) == expected


def test_inn_and_luhn_checks() -> None:
    assert inn_valid("7707083893") is True
    assert inn_valid("7707083894") is False
    assert inn_valid("500100732259") is True
    assert luhn_valid("4111111111111111") is True
    assert luhn_valid("4111111111111112") is False


def test_pii_detection_and_masking() -> None:
    assert detect_pii("user@example.ru") == "email"
    assert detect_pii("+7 916 123-45-67") == "phone"
    assert detect_pii("7707083893") == "inn"
    assert detect_pii("4111111111111111") == "card"
    assert detect_pii("просто текст") is None
    assert mask("7707083893", "inn") == "******3893"
    assert mask("user@example.ru", "email") == "u***@example.ru"


def test_profile_counts_and_types() -> None:
    profiler = _profiler()
    assert profiler.rows == 3
    segment = profiler.columns["segment"]
    assert segment.nulls == 1
    assert segment.null_ratio == pytest.approx(0.3333, abs=1e-4)
    assert profiler.columns["client_id"].distinct == {"CL001", "CL002", "CL003"}
    assert profiler.columns["amount"].type == "number"


def test_quantiles() -> None:
    profiler = _profiler()
    amount = profiler.columns["amount"]
    assert amount.quantile(0.5) == pytest.approx(2000.5)
    assert amount.quantile(0.95) == pytest.approx(2900.05, abs=0.01)


def test_distinct_cap() -> None:
    profiler = Profiler(max_distinct=2)
    profiler.feed_rows([{"id": str(index)} for index in range(10)])
    assert profiler.columns["id"].distinct_capped is True


def test_pii_columns_are_masked_in_samples() -> None:
    profiler = _profiler()
    inn = profiler.columns["inn"]
    assert "inn" in inn.pii
    assert all("*" in sample for sample in inn.masked_samples())
    assert inn.as_dict()["samples"] == inn.masked_samples()


def test_suggested_checks() -> None:
    checks = {check["check_id"]: check for check in _profiler().suggested_checks("mart.clients")}
    assert "mart.clients.segment.completeness" in checks
    assert "mart.clients.client_id.uniqueness" in checks
    assert "mart.clients.amount.range" in checks
    assert checks["mart.clients.segment.completeness"]["bounds"]["fail"]["max"] > 0.3
    assert checks["mart.clients.client_id.uniqueness"]["bounds"] == {"fail": {"max": 0}}


def test_cli_writes_artifacts(tmp_path: Path, capsys) -> None:
    markdown = tmp_path / "profile.md"
    checks = tmp_path / "checks.json"
    code = main(["profile", "--demo", "--entity", "mart.clients",
                 "--markdown", str(markdown), "--checks", str(checks)])
    output = capsys.readouterr().out
    assert code == 0
    assert "строк: 60" in output
    assert "PII" in output
    assert "| client_id |" in markdown.read_text(encoding="utf-8")
    payload = json.loads(checks.read_text(encoding="utf-8"))
    assert payload["checks"]
    assert all({"check_id", "entity", "bounds", "sql"} <= set(item) for item in payload["checks"])

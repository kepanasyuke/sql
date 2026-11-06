"""Тесты сверки: классификация расхождений, нормализация, бакеты, CLI."""

from __future__ import annotations

import csv
from pathlib import Path

from projects.recon_kit.cli import main
from projects.recon_kit.recon import (
    CHANGED,
    EXTRA,
    MISSING,
    bucket_checksums,
    canonical,
    compare,
    compare_by_buckets,
)

SOURCE = [
    {"client_id": "CL1", "turnover": "1 234,50", "segment": "МСБ"},
    {"client_id": "CL2", "turnover": "5000", "segment": "Крупный бизнес"},
    {"client_id": "CL3", "turnover": "750.00", "segment": "МСБ"},
]

TARGET = [
    {"client_id": "CL1", "turnover": "1234.5", "segment": "МСБ"},
    {"client_id": "CL2", "turnover": "5000", "segment": "Средний бизнес"},
    {"client_id": "CL4", "turnover": "100", "segment": "МСБ"},
]


def test_canonical_normalises_numbers() -> None:
    assert canonical("1 234,50") == canonical("1234.5") == "1234.5"
    assert canonical("") == ""
    assert canonical(None) == ""
    assert canonical("МСБ") == "МСБ"
    assert canonical("007") == "7"


def test_compare_classifies_rows() -> None:
    report = compare(SOURCE, TARGET, key="client_id")
    assert report.counts[MISSING] == 1          # CL3 потерян в приёмнике
    assert report.counts[EXTRA] == 1            # CL4 лишний
    assert report.counts[CHANGED] == 1          # CL2: сегмент отличается
    assert report.counts["MATCHED"] == 1        # CL1: числа в разном формате, но равны
    assert report.differences == 3
    assert report.exit_code == 1
    assert report.samples[CHANGED][0]["segment"] == {
        "source": "Крупный бизнес",
        "target": "Средний бизнес",
    }


def test_compare_ignores_columns_outside_selection() -> None:
    report = compare(SOURCE, TARGET, key="client_id", columns=["turnover"])
    assert report.counts[CHANGED] == 0
    assert report.counts["MATCHED"] == 2


def test_identical_datasets_pass() -> None:
    report = compare(SOURCE, SOURCE, key="client_id")
    assert report.is_identical
    assert report.exit_code == 0
    assert report.counts[MISSING] == 0


def test_bucket_checksums_are_order_independent() -> None:
    left = bucket_checksums(SOURCE, "client_id", buckets=4)
    right = bucket_checksums(list(reversed(SOURCE)), "client_id", buckets=4)
    assert left == right


def test_bucket_precheck_detects_difference() -> None:
    identical, left, right = compare_by_buckets(SOURCE, TARGET, "client_id", buckets=4)
    assert identical is False
    assert set(left) == set(right)


def _write_csv(path: Path, rows: list[dict]) -> Path:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    return path


def test_cli_reports_and_returns_exit_code(tmp_path: Path, capsys) -> None:
    source = _write_csv(tmp_path / "src.csv", SOURCE)
    target = _write_csv(tmp_path / "tgt.csv", TARGET)

    code = main(["run", "--source", str(source), "--target", str(target), "--key", "client_id"])
    captured = capsys.readouterr().out
    assert code == 1
    assert "Расхождений: 3" in captured

    same = _write_csv(tmp_path / "same.csv", SOURCE)
    code = main(["run", "--source", str(source), "--target", str(same), "--key", "client_id"])
    assert code == 0

"""Сверка двух датасетов: источник против приёмника.

Классификация расхождений: MISSING (есть в источнике, нет в приёмнике —
потеря данных), EXTRA (есть в приёмнике, нет в источнике — мусор или
устаревшие строки), CHANGED (ключ совпал, значения разошлись).

Память: при отсортированных входах сравнение потоковое (merge-обход, O(1)).
Checksum-бакеты дают быстрый ответ «данные совпадают» без чтения всех строк.
"""

from __future__ import annotations

import csv
import hashlib
import re
import zlib
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Iterable, Iterator, Mapping, Sequence

MISSING = "MISSING"
EXTRA = "EXTRA"
CHANGED = "CHANGED"
MATCHED = "MATCHED"

NUMERIC_RE = re.compile(r"^-?\d{1,3}(?:[ \u00a0]\d{3})*(?:[.,]\d+)?$|^-?\d+(?:[.,]\d+)?$")
SPACES_RE = re.compile(r"[ \u00a0]")


def canonical(value) -> str:
    """Канонический вид значения: '1 234,50' и '1234.5' — одно и то же число."""
    if value is None:
        text = ""
    elif isinstance(value, bool):
        text = "true" if value else "false"
    elif isinstance(value, (int, float, Decimal)):
        text = _decimal_text(Decimal(str(value)))
    else:
        text = str(value).strip()
        if text and NUMERIC_RE.match(text):
            cleaned = SPACES_RE.sub("", text).replace(",", ".")
            try:
                text = _decimal_text(Decimal(cleaned))
            except InvalidOperation:
                text = str(value).strip()
    return text


def _decimal_text(value: Decimal) -> str:
    normalized = value.normalize()
    return format(normalized, "f")


def read_csv(path: str | Path) -> list[dict[str, str]]:
    with Path(path).open("r", encoding="utf-8-sig", newline="") as handle:
        return [dict(row) for row in csv.DictReader(handle)]


def sort_rows(rows: Iterable[Mapping], key: str) -> list[dict]:
    return sorted(rows, key=lambda row: canonical(row.get(key)))


def bucket_checksums(rows: Iterable[Mapping], key: str, buckets: int = 8) -> dict[int, tuple[int, int]]:
    """Номер бакета → (количество строк, сумма хешей). Сумма не зависит от порядка.

    Бакет считается по crc32 ключа (быстро и детерминированно между процессами),
    контрольная сумма строки — по одному sha256 на строку.
    """
    result: dict[int, tuple[int, int]] = {index: (0, 0) for index in range(buckets)}
    for row in rows:
        bucket = zlib.crc32(canonical(row.get(key)).encode()) % buckets
        payload = "|".join(f"{name}={canonical(row[name])}" for name in sorted(row)).encode()
        checksum = int.from_bytes(hashlib.sha256(payload).digest()[:8], "big")
        count, total = result[bucket]
        result[bucket] = (count + 1, (total + checksum) % (2**64))
    return result


@dataclass
class ReconReport:
    key: str
    compared_columns: list[str]
    counts: dict[str, int] = field(default_factory=dict)
    samples: dict[str, list[dict]] = field(default_factory=dict)
    buckets_compared: int = 0
    buckets_matched: int = 0
    rows_source: int = 0
    rows_target: int = 0

    @property
    def differences(self) -> int:
        return sum(self.counts.get(status, 0) for status in (MISSING, EXTRA, CHANGED))

    @property
    def is_identical(self) -> bool:
        return self.differences == 0

    @property
    def exit_code(self) -> int:
        return 0 if self.is_identical else 1

    def to_dict(self) -> dict:
        return {
            "key": self.key,
            "columns": self.compared_columns,
            "rows_source": self.rows_source,
            "rows_target": self.rows_target,
            "counts": self.counts,
            "buckets": {"compared": self.buckets_compared, "matched": self.buckets_matched},
            "samples": self.samples,
        }

    def to_markdown(self) -> str:
        lines = [
            "# Отчёт сверки",
            "",
            f"- Ключ: `{self.key}`",
            f"- Сравниваемые колонки: {', '.join(f'`{c}`' for c in self.compared_columns) or 'все'}",
            f"- Строк в источнике: {self.rows_source}",
            f"- Строк в приёмнике: {self.rows_target}",
            f"- Совпавшие бакеты checksum: {self.buckets_matched} из {self.buckets_compared}",
            f"- **Расхождений: {self.differences}**",
            "",
            "| Статус | Строк | Что означает |",
            "| --- | --- | --- |",
            f"| MISSING | {self.counts.get(MISSING, 0)} | есть в источнике, потеряно в приёмнике |",
            f"| EXTRA | {self.counts.get(EXTRA, 0)} | есть в приёмнике, нет в источнике |",
            f"| CHANGED | {self.counts.get(CHANGED, 0)} | ключ совпал, значения разошлись |",
            f"| MATCHED | {self.counts.get(MATCHED, 0)} | полностью совпало |",
            "",
        ]
        for status in (MISSING, EXTRA, CHANGED):
            rows = self.samples.get(status) or []
            if rows:
                keys = list(rows[0].keys())
                lines.append(f"## {status}")
                lines.append("")
                lines.append("| " + " | ".join(keys) + " |")
                lines.append("| " + " | ".join("---" for _ in keys) + " |")
                for row in rows:
                    lines.append("| " + " | ".join(str(row.get(k, "")) for k in keys) + " |")
                lines.append("")
        return "\n".join(lines)


def _merge_walk(
    source: Sequence[Mapping], target: Sequence[Mapping], key: str
) -> Iterator[tuple[str, Mapping, Mapping | None]]:
    left = right = 0
    while left < len(source) and right < len(target):
        source_key = canonical(source[left].get(key))
        target_key = canonical(target[right].get(key))
        if source_key < target_key:
            yield MISSING, source[left], None
            left += 1
        elif source_key > target_key:
            yield EXTRA, target[right], None
            right += 1
        else:
            yield "both", source[left], target[right]
            left += 1
            right += 1
    for row in source[left:]:
        yield MISSING, row, None
    for row in target[right:]:
        yield EXTRA, row, None


def compare(
    source: Iterable[Mapping],
    target: Iterable[Mapping],
    key: str,
    columns: Sequence[str] | None = None,
    sample_limit: int = 5,
    presorted: bool = False,
) -> ReconReport:
    source_rows = list(source)
    target_rows = list(target)
    if not presorted:
        source_rows = sort_rows(source_rows, key)
        target_rows = sort_rows(target_rows, key)

    compared = list(columns) if columns else sorted(
        {name for row in source_rows for name in row} - {key}
    )
    report = ReconReport(
        key=key,
        compared_columns=compared,
        counts={MISSING: 0, EXTRA: 0, CHANGED: 0, MATCHED: 0},
        rows_source=len(source_rows),
        rows_target=len(target_rows),
        samples={MISSING: [], EXTRA: [], CHANGED: []},
    )

    for status, source_row, target_row in _merge_walk(source_rows, target_rows, key):
        if status in (MISSING, EXTRA):
            report.counts[status] = report.counts.get(status, 0) + 1
            if len(report.samples[status]) < sample_limit:
                report.samples[status].append(_cut(source_row, key, compared))
        else:
            diff = {
                column: {"source": source_row.get(column), "target": (target_row or {}).get(column)}
                for column in compared
                if canonical(source_row.get(column)) != canonical((target_row or {}).get(column))
            }
            if diff:
                report.counts[CHANGED] = report.counts.get(CHANGED, 0) + 1
                if len(report.samples[CHANGED]) < sample_limit:
                    report.samples[CHANGED].append({key: source_row.get(key), **diff})
            else:
                report.counts[MATCHED] = report.counts.get(MATCHED, 0) + 1
    return report


def _cut(row: Mapping, key: str, columns: Sequence[str]) -> dict:
    return {key: row.get(key), **{column: row.get(column) for column in columns}}


def compare_by_buckets(
    source: Iterable[Mapping], target: Iterable[Mapping], key: str, buckets: int = 8
) -> tuple[bool, dict[int, tuple[int, int]], dict[int, tuple[int, int]]]:
    """Быстрый предварительный шаг: если бакеты совпали, сверка не нужна."""
    left = bucket_checksums(source, key, buckets)
    right = bucket_checksums(target, key, buckets)
    return left == right, left, right

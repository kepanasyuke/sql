"""Профилирование CSV: типы, полнота, распределение, PII, подсказки для DQ.

Профиль нужен, чтобы проверки писались по данным, а не по интуиции: здесь
появляются реальные доли NULL, границы значений и квантили, из которых потом
рождаются пороги `fail`/`warn`.
"""

from __future__ import annotations

import csv
import re
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Iterable, Mapping

EMAIL_RE = re.compile(r"^[\w.+-]+@[\w-]+\.[\w.]+$")
PHONE_RE = re.compile(r"^(\+7|8)[\s(-]*\d{3}[\s)-]*\d{3}[\s-]*\d{2}[\s-]*\d{2}$")
CARD_RE = re.compile(r"^\d{16}$")
INN_RE = re.compile(r"^\d{10}$|^\d{12}$")

INN_W10 = (2, 4, 10, 3, 5, 9, 4, 6, 8)
INN_W11 = (7, 2, 4, 10, 3, 5, 9, 4, 6, 8)
INN_W12 = (3, 7, 2, 4, 10, 3, 5, 9, 4, 6, 8)

SAMPLE_LIMIT = 5000
DEFAULT_MAX_DISTINCT = 200


def _checksum(digits: str, weights: tuple[int, ...]) -> int:
    return sum(int(d) * w for d, w in zip(digits, weights)) % 11 % 10


def inn_valid(value: str) -> bool:
    if not INN_RE.match(value):
        return False
    if len(value) == 10:
        return _checksum(value[:9], INN_W10) == int(value[9])
    return (
        _checksum(value[:10], INN_W11) == int(value[10])
        and _checksum(value[:11], INN_W12) == int(value[11])
    )


def luhn_valid(value: str) -> bool:
    if not CARD_RE.match(value):
        return False
    total = 0
    for index, char in enumerate(reversed(value)):
        digit = int(char)
        if index % 2 == 1:
            digit *= 2
            if digit > 9:
                digit -= 9
        total += digit
    return total % 10 == 0


def detect_pii(value: str) -> str | None:
    if EMAIL_RE.match(value):
        return "email"
    if PHONE_RE.match(value):
        return "phone"
    if inn_valid(value):
        return "inn"
    if luhn_valid(value):
        return "card"
    return None


def mask(value: str, pii_type: str) -> str:
    if pii_type == "email":
        local, _, domain = value.partition("@")
        return f"{local[:1]}***@{domain}"
    digits = re.sub(r"\D", "", value)
    if len(digits) <= 4:
        return "*" * len(value)
    hidden = "*" * (len(value) - 4)
    return f"{hidden}{value[-4:]}"


def infer_type(value: str) -> str:
    if re.fullmatch(r"-?\d+", value):
        return "integer"
    if re.fullmatch(r"-?\d+[.,]\d+", value):
        return "number"
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        try:
            date.fromisoformat(value)
            return "date"
        except ValueError:
            return "text"
    if value.lower() in ("true", "false"):
        return "boolean"
    return "text"


@dataclass
class ColumnProfile:
    name: str
    rows: int = 0
    nulls: int = 0
    types: dict[str, int] = field(default_factory=dict)
    distinct: set[str] = field(default_factory=set)
    distinct_capped: bool = False
    numeric: list[float] = field(default_factory=list)
    min_value: str | None = None
    max_value: str | None = None
    pii: dict[str, int] = field(default_factory=dict)
    samples: list[str] = field(default_factory=list)

    @property
    def null_ratio(self) -> float:
        return round(self.nulls / self.rows, 4) if self.rows else 0.0

    @property
    def type(self) -> str:
        if not self.types:
            return "unknown"
        total = sum(self.types.values())
        numeric = self.types.get("integer", 0) + self.types.get("number", 0)
        if numeric >= 0.95 * total:
            return "number"
        for name in ("date", "boolean"):
            if self.types.get(name, 0) >= 0.95 * total:
                return name
        return "text"

    def masked_samples(self) -> list[str]:
        """Если в колонке найден PII, маскируются все примеры, а не только они."""
        if not self.pii:
            return list(self.samples)
        return [sample if "*" in sample else mask(sample, "inn") for sample in self.samples]

    def quantile(self, q: float) -> float | None:
        if not self.numeric:
            return None
        ordered = sorted(self.numeric)
        position = (len(ordered) - 1) * q
        low = int(position)
        high = min(low + 1, len(ordered) - 1)
        weight = position - low
        return round(ordered[low] * (1 - weight) + ordered[high] * weight, 4)

    def as_dict(self) -> dict:
        return {
            "column": self.name,
            "type": self.type,
            "nulls": self.nulls,
            "null_ratio": self.null_ratio,
            "distinct": len(self.distinct),
            "distinct_capped": self.distinct_capped,
            "min": self.min_value,
            "max": self.max_value,
            "p50": self.quantile(0.5),
            "p95": self.quantile(0.95),
            "pii": sorted(self.pii),
            "samples": self.masked_samples(),
        }


class Profiler:
    def __init__(self, max_distinct: int = DEFAULT_MAX_DISTINCT, sample_size: int = 3):
        self.max_distinct = max_distinct
        self.sample_size = sample_size
        self.columns: dict[str, ColumnProfile] = {}
        self.rows = 0

    def feed(self, row: Mapping[str, str]) -> None:
        self.rows += 1
        for name, raw in row.items():
            profile = self.columns.setdefault(name, ColumnProfile(name=name))
            profile.rows += 1
            value = (raw or "").strip()
            if value == "":
                profile.nulls += 1
                continue

            profile.types[infer_type(value)] = profile.types.get(infer_type(value), 0) + 1
            if not profile.distinct_capped:
                profile.distinct.add(value)
                if len(profile.distinct) > self.max_distinct:
                    profile.distinct_capped = True

            pii_type = detect_pii(value)
            if pii_type:
                profile.pii[pii_type] = profile.pii.get(pii_type, 0) + 1
                if len(profile.samples) < self.sample_size:
                    profile.samples.append(mask(value, pii_type))
                continue

            if len(profile.samples) < self.sample_size:
                profile.samples.append(value)
            numeric_type = infer_type(value)
            if numeric_type in ("integer", "number"):
                try:
                    number = float(value.replace(",", "."))
                except ValueError:
                    number = None
                if number is not None and len(profile.numeric) < SAMPLE_LIMIT:
                    profile.numeric.append(number)
            if profile.min_value is None or value < profile.min_value:
                profile.min_value = value
            if profile.max_value is None or value > profile.max_value:
                profile.max_value = value

    def feed_rows(self, rows: Iterable[Mapping[str, str]]) -> None:
        for row in rows:
            self.feed(row)

    def as_dict(self) -> dict:
        return {
            "rows": self.rows,
            "columns": [self.columns[name].as_dict() for name in self.columns],
        }

    def to_markdown(self, title: str = "Профиль данных") -> str:
        lines = [f"# {title}", "", f"Строк: {self.rows}", ""]
        lines.append("| Колонка | Тип | NULL, % | Distinct | Min | Max | p50 | p95 | PII |")
        lines.append("| --- | --- | --- | --- | --- | --- | --- | --- | --- |")
        for name, column in self.columns.items():
            distinct = f"> {self.max_distinct}" if column.distinct_capped else str(len(column.distinct))
            lines.append(
                f"| {name} | {column.type} | {column.null_ratio * 100:.1f} | {distinct} | "
                f"{column.min_value or '-'} | {column.max_value or '-'} | "
                f"{column.quantile(0.5) if column.quantile(0.5) is not None else '-'} | "
                f"{column.quantile(0.95) if column.quantile(0.95) is not None else '-'} | "
                f"{', '.join(sorted(column.pii)) or '-'} |"
            )
        lines.append("")
        pii_columns = [name for name, column in self.columns.items() if column.pii]
        if pii_columns:
            lines.append(f"**PII найдены в колонках:** {', '.join(pii_columns)}")
            lines.append("")
        return "\n".join(lines)

    def suggested_checks(self, entity: str) -> list[dict]:
        checks: list[dict] = []
        for name, column in self.columns.items():
            if column.null_ratio > 0:
                checks.append(
                    {
                        "check_id": f"{entity}.{name}.completeness",
                        "title": f"Полнота {name}",
                        "entity": entity,
                        "severity": "P2",
                        "unit": "ratio",
                        "bounds": {"fail": {"max": round(min(column.null_ratio * 2 + 0.01, 0.99), 3)},
                                   "warn": {"max": round(column.null_ratio + 0.005, 3)}},
                        "description": f"В профиле {column.null_ratio:.1%} пустых значений",
                        "sql": f"SELECT ROUND(AVG(CASE WHEN {name} IS NULL OR TRIM({name}) = '' THEN 1.0 ELSE 0.0 END), 4)"
                               f" AS metric_value, COUNT(*) AS rows_examined FROM {entity}",
                    }
                )
            is_key_candidate = (
                not column.distinct_capped
                and column.nulls == 0
                and column.rows > 0
                and len(column.distinct) / max(column.rows, 1) > 0.99
            )
            if is_key_candidate:
                checks.append(
                    {
                        "check_id": f"{entity}.{name}.uniqueness",
                        "title": f"Уникальность {name}",
                        "entity": entity,
                        "severity": "P2",
                        "unit": "count",
                        "bounds": {"fail": {"max": 0}},
                        "description": "Колонка выглядит бизнес-ключом: уникальна и без NULL",
                        "sql": f"SELECT COUNT(*) AS metric_value FROM (SELECT {name} FROM {entity}"
                               f" GROUP BY {name} HAVING COUNT(*) > 1) d",
                    }
                )
            if column.numeric and column.quantile(0.95) is not None:
                upper = round((column.quantile(0.95) or 0) * 3, 2)
                checks.append(
                    {
                        "check_id": f"{entity}.{name}.range",
                        "title": f"Диапазон {name}",
                        "entity": entity,
                        "severity": "P3",
                        "unit": "count",
                        "bounds": {"fail": {"max": 0}},
                        "description": f"Порог подобран по p95 = {column.quantile(0.95)}",
                        "sql": f"SELECT COUNT(*) AS metric_value FROM {entity} WHERE {name} > {upper}",
                    }
                )
        return checks


def read_csv(path: str | Path) -> list[dict[str, str]]:
    with Path(path).open("r", encoding="utf-8-sig", newline="") as handle:
        return [dict(row) for row in csv.DictReader(handle)]


def profile_file(path: str | Path, max_distinct: int = DEFAULT_MAX_DISTINCT) -> Profiler:
    profiler = Profiler(max_distinct=max_distinct)
    profiler.feed_rows(read_csv(path))
    return profiler

"""Профилирование CSV: типы, полнота, распределение, PII, подсказки для DQ.

Профиль нужен, чтобы проверки писались по данным, а не по интуиции: здесь
появляются реальные доли NULL, границы значений и квантили, из которых потом
рождаются пороги `fail`/`warn`.

Горячий путь — `Profiler.feed`: одно значение разбирается один раз, тип
вычисляется однажды, а проверка на PII имеет быстрый отсев, чтобы регулярки
не запускались на каждой текстовой колонке.
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
INT_RE = re.compile(r"-?\d+")
NUM_RE = re.compile(r"-?\d+[.,]\d+")
DATE_RE = re.compile(r"\d{4}-\d{2}-\d{2}")

INN_W10 = (2, 4, 10, 3, 5, 9, 4, 6, 8)
INN_W11 = (7, 2, 4, 10, 3, 5, 9, 4, 6, 8)
INN_W12 = (3, 7, 2, 4, 10, 3, 5, 9, 4, 6, 8)

SAMPLE_LIMIT = 5000
DEFAULT_MAX_DISTINCT = 200
_PII_HINT_CHARS = frozenset("0123456789+@")


def _checksum(digits: str, weights: tuple[int, ...]) -> int:
    return sum(int(d) * w for d, w in zip(digits, weights)) % 11 % 10


def inn_valid(value: str) -> bool:
    valid = False
    if INN_RE.match(value):
        if len(value) == 10:
            valid = _checksum(value[:9], INN_W10) == int(value[9])
        else:
            valid = (
                _checksum(value[:10], INN_W11) == int(value[10])
                and _checksum(value[:11], INN_W12) == int(value[11])
            )
    return valid


def luhn_valid(value: str) -> bool:
    valid = False
    if CARD_RE.match(value):
        total = 0
        for index, char in enumerate(reversed(value)):
            digit = int(char)
            if index % 2 == 1:
                digit *= 2
                if digit > 9:
                    digit -= 9
            total += digit
        valid = total % 10 == 0
    return valid


def detect_pii(value: str) -> str | None:
    """Быстрый отсев по символам: регулярки запускаются только там, где есть шанс.

    На колонках вроде `segment` («МСБ», «Крупный бизнес») это убирает четыре
    регулярных выражения на каждое значение.
    """
    pii_type: str | None = None
    if any(char in _PII_HINT_CHARS for char in value):
        if "@" in value:
            pii_type = "email" if EMAIL_RE.match(value) else None
        elif value[:1] in ("+", "8", "7"):
            pii_type = "phone" if PHONE_RE.match(value) else None
        if pii_type is None:
            if inn_valid(value):
                pii_type = "inn"
            elif luhn_valid(value):
                pii_type = "card"
    return pii_type


def mask(value: str, pii_type: str) -> str:
    if pii_type == "email":
        local, _, domain = value.partition("@")
        masked = f"{local[:1]}***@{domain}"
    else:
        digits = re.sub(r"\D", "", value)
        masked = "*" * len(value) if len(digits) <= 4 else f"{'*' * (len(value) - 4)}{value[-4:]}"
    return masked


def infer_type(value: str) -> str:
    """Тип значения. Быстрый путь: без цифры в начале строка не может быть числом."""
    detected = "text"
    if value[:1] in ("-", "0", "1", "2", "3", "4", "5", "6", "7", "8", "9"):
        if INT_RE.fullmatch(value):
            detected = "integer"
        elif NUM_RE.fullmatch(value):
            detected = "number"
        elif DATE_RE.fullmatch(value):
            try:
                date.fromisoformat(value)
                detected = "date"
            except ValueError:
                detected = "text"
    elif value.lower() in ("true", "false"):
        detected = "boolean"
    return detected


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
    _sorted_numeric: list[float] | None = field(default=None, repr=False)

    @property
    def null_ratio(self) -> float:
        return round(self.nulls / self.rows, 4) if self.rows else 0.0

    @property
    def type(self) -> str:
        detected = "unknown"
        if self.types:
            total = sum(self.types.values())
            numeric = self.types.get("integer", 0) + self.types.get("number", 0)
            if numeric >= 0.95 * total:
                detected = "number"
            elif self.types.get("date", 0) >= 0.95 * total:
                detected = "date"
            elif self.types.get("boolean", 0) >= 0.95 * total:
                detected = "boolean"
            else:
                detected = "text"
            if self.pii and detected in ("number", "integer"):
                detected = "text"     # ИНН/карта/телефон - идентификаторы, а не числа
        return detected

    def add_value(self, value: str, value_type: str, pii_type: str | None, max_distinct: int, sample_size: int) -> None:
        """Одно значение: тип уже вычислен вызывающим кодом (не пересчитываем)."""
        self.rows += 1
        if value == "":
            self.nulls += 1
            return

        self.types[value_type] = self.types.get(value_type, 0) + 1
        if not self.distinct_capped:
            self.distinct.add(value)
            self.distinct_capped = len(self.distinct) > max_distinct

        if pii_type is not None:
            self.pii[pii_type] = self.pii.get(pii_type, 0) + 1
            if len(self.samples) < sample_size:
                self.samples.append(mask(value, pii_type))
        else:
            if len(self.samples) < sample_size:
                self.samples.append(value)
            if value_type in ("integer", "number"):
                self._add_numeric(value)
            if self.min_value is None or value < self.min_value:
                self.min_value = value
            if self.max_value is None or value > self.max_value:
                self.max_value = value

    def _add_numeric(self, value: str) -> None:
        if len(self.numeric) < SAMPLE_LIMIT:
            try:
                self.numeric.append(float(value.replace(",", ".")))
                self._sorted_numeric = None
            except ValueError:
                pass

    def masked_samples(self) -> list[str]:
        """Если в колонке найден PII, маскируются все примеры, а не только они."""
        samples = list(self.samples)
        if self.pii:
            samples = [sample if "*" in sample else mask(sample, "inn") for sample in samples]
        return samples

    def quantile(self, q: float) -> float | None:
        result = None
        if self.numeric:
            if self._sorted_numeric is None:
                self._sorted_numeric = sorted(self.numeric)
            ordered = self._sorted_numeric
            position = (len(ordered) - 1) * q
            low = int(position)
            high = min(low + 1, len(ordered) - 1)
            weight = position - low
            result = round(ordered[low] * (1 - weight) + ordered[high] * weight, 4)
        return result

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
            profile = self.columns.get(name)
            if profile is None:
                profile = self.columns[name] = ColumnProfile(name=name)
            value = (raw or "").strip()
            value_type = infer_type(value) if value else "text"
            profile.add_value(
                value=value,
                value_type=value_type,
                pii_type=detect_pii(value) if value else None,
                max_distinct=self.max_distinct,
                sample_size=self.sample_size,
            )

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
            p50, p95 = column.quantile(0.5), column.quantile(0.95)
            lines.append(
                f"| {name} | {column.type} | {column.null_ratio * 100:.1f} | {distinct} | "
                f"{column.min_value or '-'} | {column.max_value or '-'} | "
                f"{p50 if p50 is not None else '-'} | {p95 if p95 is not None else '-'} | "
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
            upper_quantile = column.quantile(0.95)
            if column.numeric and upper_quantile is not None:
                upper = round(upper_quantile * 3, 2)
                checks.append(
                    {
                        "check_id": f"{entity}.{name}.range",
                        "title": f"Диапазон {name}",
                        "entity": entity,
                        "severity": "P3",
                        "unit": "count",
                        "bounds": {"fail": {"max": 0}},
                        "description": f"Порог подобран по p95 = {upper_quantile}",
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

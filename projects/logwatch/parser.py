"""Разбор логов: nginx combined и JSON-строки приложения."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

NGINX_RE = re.compile(
    r'^(?P<ip>\S+) \S+ \S+ \[(?P<ts>[^\]]+)\] "(?P<method>[A-Z]+) (?P<path>\S+)[^"]*" '
    r'(?P<status>\d{3}) (?P<bytes>\d+)'
)


@dataclass
class LogRecord:
    raw: str
    ts: datetime | None = None
    level: str = "info"
    method: str | None = None
    path: str | None = None
    status: int | None = None
    duration_ms: float | None = None
    fields: dict[str, Any] = field(default_factory=dict)

    @property
    def fingerprint(self) -> str:
        """Что считаем «одной и той же проблемой»: маршрут + статус."""
        return f"{self.method or '-'} {self.path or '-'} {self.status or '-'}"

    @property
    def severity_level(self) -> int:
        levels = {"debug": 0, "info": 1, "warning": 2, "warn": 2, "error": 3, "critical": 4, "fatal": 4}
        return levels.get(self.level.lower(), 1)


TS_FORMATS = ("%d/%b/%Y:%H:%M:%S %z", "%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S")


def parse_ts(value: str) -> datetime | None:
    """Первый формат, который разобрался; None, если ни один не подошёл."""
    text = value.replace("Z", "+0000") if value.endswith("Z") else value
    parsed = None
    index = 0
    while index < len(TS_FORMATS) and parsed is None:
        try:
            candidate = datetime.strptime(text, TS_FORMATS[index])
        except ValueError:
            candidate = None
        if candidate is not None:
            parsed = candidate if candidate.tzinfo else candidate.replace(tzinfo=timezone.utc)
        index += 1
    return parsed


def _parse_json_line(text: str) -> LogRecord | None:
    record = None
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        payload = None
    if payload is not None:
        record = LogRecord(raw=text, fields=payload)
        record.ts = parse_ts(str(payload.get("ts") or payload.get("time") or ""))
        record.level = str(payload.get("level", "info")).lower()
        record.method = payload.get("method")
        record.path = payload.get("path") or payload.get("url")
        status = payload.get("status") or payload.get("status_code")
        record.status = int(status) if status is not None else None
        duration = payload.get("duration_ms") or payload.get("latency_ms")
        record.duration_ms = float(duration) if duration is not None else None
    return record


def _parse_nginx_line(text: str) -> LogRecord | None:
    match = NGINX_RE.match(text)
    record = LogRecord(raw=text) if match else None
    if match and record is not None:
        record.ts = parse_ts(match.group("ts"))
        record.method = match.group("method")
        record.path = match.group("path").split("?")[0]
        record.status = int(match.group("status"))
        record.level = "error" if record.status and record.status >= 500 else "info"
    return record


def parse_line(line: str) -> LogRecord | None:
    """Строка лога → запись. Формат определяется по первому символу."""
    text = line.strip()
    record = None
    if text:
        record = _parse_json_line(text) if text.startswith("{") else _parse_nginx_line(text)
    return record


def parse_stream(lines) -> list[LogRecord]:
    records = []
    for line in lines:
        record = parse_line(line)
        if record is not None:
            records.append(record)
    return records

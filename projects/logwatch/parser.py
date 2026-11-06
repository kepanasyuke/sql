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


def parse_ts(value: str) -> datetime | None:
    formats = ("%d/%b/%Y:%H:%M:%S %z", "%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S")
    for fmt in formats:
        try:
            parsed = datetime.strptime(value.replace("Z", "+0000") if value.endswith("Z") else value, fmt)
            return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return None


def parse_line(line: str) -> LogRecord | None:
    text = line.strip()
    if not text:
        return None

    if text.startswith("{"):
        try:
            payload = json.loads(text)
        except json.JSONDecodeError:
            return None
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

    match = NGINX_RE.match(text)
    if not match:
        return None
    record = LogRecord(raw=text)
    record.ts = parse_ts(match.group("ts"))
    record.method = match.group("method")
    record.path = match.group("path").split("?")[0]
    record.status = int(match.group("status"))
    record.level = "error" if record.status and record.status >= 500 else "info"
    return record


def parse_stream(lines) -> list[LogRecord]:
    records = []
    for line in lines:
        record = parse_line(line)
        if record is not None:
            records.append(record)
    return records

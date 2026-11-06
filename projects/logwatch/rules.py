"""Правила алертинга: пороги по окну, дедупликация, счётчики подавления."""

from __future__ import annotations

import re
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Iterable

from .parser import LogRecord


@dataclass
class Alert:
    rule_id: str
    severity: str
    message: str
    fingerprint: str
    count: int
    first_seen: str
    last_seen: str
    suppressed: int
    sample: str

    def as_dict(self) -> dict:
        return {
            "rule": self.rule_id,
            "severity": self.severity,
            "message": self.message,
            "fingerprint": self.fingerprint,
            "count": self.count,
            "suppressed": self.suppressed,
            "first_seen": self.first_seen,
            "last_seen": self.last_seen,
        }


@dataclass
class Rule:
    rule_id: str
    severity: str = "P2"
    min_count: int = 1
    window_seconds: int = 60
    dedup_seconds: int = 300
    statuses: tuple[int, ...] = ()
    status_at_least: int | None = None
    pattern: str | None = None
    min_level: str | None = None
    min_duration_ms: float | None = None
    title: str = ""

    def matches(self, record: LogRecord) -> bool:
        if self.statuses and record.status not in self.statuses:
            return False
        if self.status_at_least is not None and (record.status or 0) < self.status_at_least:
            return False
        if self.pattern and not re.search(self.pattern, record.raw, re.IGNORECASE):
            return False
        if self.min_level and record.severity_level < _level(self.min_level):
            return False
        if self.min_duration_ms is not None and (record.duration_ms or 0) < self.min_duration_ms:
            return False
        return True


def _level(name: str) -> int:
    return {"debug": 0, "info": 1, "warning": 2, "warn": 2, "error": 3, "critical": 4, "fatal": 4}.get(
        name.lower(), 1
    )


@dataclass
class _RuleState:
    hits: deque[datetime] = field(default_factory=deque)
    last_alert: dict[str, datetime] = field(default_factory=dict)
    suppressed: dict[str, int] = field(default_factory=dict)


class RuleEngine:
    """Потоковый алертинг: окно по времени, дедуп по отпечатку проблемы."""

    def __init__(self, rules: Iterable[Rule]):
        self.rules = list(rules)
        self._state: dict[str, _RuleState] = {rule.rule_id: _RuleState() for rule in self.rules}
        self.alerts: list[Alert] = []
        self.counters: dict[str, int] = {"events": 0, "alerts": 0, "suppressed": 0}
        self._last_ts: datetime | None = None

    def _now(self, record: LogRecord) -> datetime:
        if record.ts is not None:
            self._last_ts = record.ts
            return record.ts
        self._last_ts = (self._last_ts or datetime.now(timezone.utc)) + timedelta(seconds=1)
        return self._last_ts

    def feed(self, record: LogRecord) -> list[Alert]:
        self.counters["events"] += 1
        now = self._now(record)
        produced: list[Alert] = []
        for rule in self.rules:
            if not rule.matches(record):
                continue
            state = self._state[rule.rule_id]
            state.hits.append(now)
            edge = now - timedelta(seconds=rule.window_seconds)
            while state.hits and state.hits[0] < edge:
                state.hits.popleft()
            if len(state.hits) < rule.min_count:
                continue

            fingerprint = f"{rule.rule_id}|{record.fingerprint}"
            last = state.last_alert.get(fingerprint)
            if last is not None and (now - last).total_seconds() < rule.dedup_seconds:
                state.suppressed[fingerprint] = state.suppressed.get(fingerprint, 0) + 1
                self.counters["suppressed"] += 1
                continue

            suppressed = state.suppressed.pop(fingerprint, 0)
            state.last_alert[fingerprint] = now
            alert = Alert(
                rule_id=rule.rule_id,
                severity=rule.severity,
                message=rule.title or rule.rule_id,
                fingerprint=record.fingerprint,
                count=len(state.hits),
                first_seen=state.hits[0].isoformat(timespec="seconds"),
                last_seen=now.isoformat(timespec="seconds"),
                suppressed=suppressed,
                sample=record.raw[:200],
            )
            self.counters["alerts"] += 1
            self.alerts.append(alert)
            produced.append(alert)
        return produced

    def feed_all(self, records: Iterable[LogRecord]) -> list[Alert]:
        produced: list[Alert] = []
        for record in records:
            produced.extend(self.feed(record))
        return produced


def default_rules() -> list[Rule]:
    return [
        Rule(
            rule_id="http.5xx_burst",
            severity="P1",
            status_at_least=500,
            min_count=3,
            window_seconds=60,
            title="серия ответов 5xx",
        ),
        Rule(
            rule_id="app.error",
            severity="P2",
            min_level="error",
            min_count=1,
            window_seconds=60,
            title="ошибки в логе приложения",
        ),
        Rule(
            rule_id="db.pool_exhausted",
            severity="P1",
            pattern=r"pool exhausted|too many connections|deadlock",
            min_count=1,
            window_seconds=300,
            title="проблемы с пулом соединений БД",
        ),
        Rule(
            rule_id="http.slow_request",
            severity="P3",
            min_duration_ms=1500,
            min_count=1,
            window_seconds=300,
            title="медленный запрос",
        ),
    ]

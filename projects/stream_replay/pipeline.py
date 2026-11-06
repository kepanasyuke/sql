"""Потоковая обработка событий: дедупликация, tumbling-окна, водяной знак.

Модель событий: `event_id`, `ts` (event time), `client_id`, `amount`.
Ключевые свойства:
  * дубликаты (at-least-once доставка) не влияют на суммы;
  * опоздавшие события в пределах `allowed_lateness` попадают в своё окно;
  * события, опоздавшие сильнее, честно считаются потерянными (`late_dropped`),
    а не молча растворяются;
  * окно закрывается, когда водяной знак (max_ts - lateness) перевалил за его конец.
"""

from __future__ import annotations

import random
from collections import deque
from dataclasses import dataclass, field
from typing import Iterable


@dataclass(frozen=True)
class Event:
    event_id: str
    ts: float
    client_id: str
    amount: float
    kind: str = "turnover"

    def as_dict(self) -> dict:
        return {
            "event_id": self.event_id,
            "ts": self.ts,
            "client_id": self.client_id,
            "amount": self.amount,
        }


@dataclass
class Deduper:
    """Окно дедупликации ограниченного размера (иначе память растёт бесконечно)."""

    capacity: int = 10_000
    _seen: set[str] = field(default_factory=set)
    _order: deque[str] = field(default_factory=deque)
    duplicates: int = 0

    def is_new(self, event_id: str) -> bool:
        if event_id in self._seen:
            self.duplicates += 1
            return False
        self._seen.add(event_id)
        self._order.append(event_id)
        if len(self._order) > self.capacity:
            oldest = self._order.popleft()
            self._seen.discard(oldest)
        return True


@dataclass
class WindowAggregator:
    window_seconds: int = 60
    allowed_lateness: int = 30
    windows: dict[float, dict[str, float]] = field(default_factory=dict)
    counts: dict[float, int] = field(default_factory=dict)
    closed: list[dict] = field(default_factory=list)
    max_ts: float = float("-inf")
    watermark: float = float("-inf")
    late_dropped: int = 0

    def add(self, event: Event) -> list[dict]:
        if event.ts < self.watermark:
            self.late_dropped += 1
            return []
        self.max_ts = max(self.max_ts, event.ts)
        start = self._window_start(event.ts)
        bucket = self.windows.setdefault(start, {})
        bucket[event.client_id] = round(bucket.get(event.client_id, 0.0) + event.amount, 2)
        self.counts[start] = self.counts.get(start, 0) + 1

        self.watermark = max(self.watermark, self.max_ts - self.allowed_lateness)
        emitted = []
        for window_start in sorted(self.windows):
            if window_start + self.window_seconds <= self.watermark:
                emitted.append(self._close(window_start))
        return emitted

    def flush(self) -> list[dict]:
        return [self._close(start) for start in sorted(self.windows)]

    def _window_start(self, ts: float) -> float:
        return float(int(ts // self.window_seconds) * self.window_seconds)

    def _close(self, start: float) -> dict:
        totals = self.windows.pop(start)
        count = self.counts.pop(start)
        window = {
            "window_start": int(start),
            "window_end": int(start) + self.window_seconds,
            "events": count,
            "clients": len(totals),
            "total_amount": round(sum(totals.values()), 2),
            "top_client": max(totals.items(), key=lambda item: item[1])[0] if totals else None,
        }
        self.closed.append(window)
        return window


@dataclass
class StreamPipeline:
    window_seconds: int = 60
    allowed_lateness: int = 30
    dedupe_capacity: int = 10_000
    aggregator: WindowAggregator = field(init=False)
    deduper: Deduper = field(init=False)
    received: int = 0

    def __post_init__(self) -> None:
        self.aggregator = WindowAggregator(self.window_seconds, self.allowed_lateness)
        self.deduper = Deduper(self.dedupe_capacity)

    def process(self, events: Iterable[Event]) -> dict:
        for event in events:
            self.received += 1
            if not self.deduper.is_new(event.event_id):
                continue
            self.aggregator.add(event)
        return {"windows": self.aggregator.closed, "metrics": self.metrics()}

    def finish(self) -> dict:
        self.aggregator.flush()
        return {"windows": self.aggregator.closed, "metrics": self.metrics()}

    def metrics(self) -> dict:
        return {
            "received": self.received,
            "duplicates": self.deduper.duplicates,
            "late_dropped": self.aggregator.late_dropped,
            "windows_closed": len(self.aggregator.closed),
            "watermark": None if self.aggregator.watermark == float("-inf") else int(self.aggregator.watermark),
        }


def generate_events(
    count: int = 300,
    clients: int = 8,
    start_ts: float = 1_760_000_000.0,
    span_seconds: int = 300,
    duplicate_ratio: float = 0.15,
    late_ratio: float = 0.03,
    seed: int = 11,
) -> list[Event]:
    rng = random.Random(seed)
    events: list[Event] = []
    for index in range(count):
        ts = start_ts + rng.uniform(0, span_seconds)
        events.append(
            Event(
                event_id=f"EV{index:06d}",
                ts=round(ts, 1),
                client_id=f"CL{rng.randint(1, clients):03d}",
                amount=round(rng.uniform(1000, 250_000), 2),
            )
        )
    events.sort(key=lambda event: event.ts)
    for index in rng.sample(range(len(events)), k=int(len(events) * duplicate_ratio)):
        events.append(events[index])  # at-least-once: событие доставлено повторно
    for index in rng.sample(range(20, len(events)), k=max(1, int(len(events) * late_ratio))):
        original = events[index]
        lag = rng.uniform(90, 240)
        events.append(
            Event(
                event_id=f"{original.event_id}-late",
                ts=round(original.ts - lag, 1),
                client_id=original.client_id,
                amount=original.amount,
            )
        )
    rng.shuffle(events)
    return events

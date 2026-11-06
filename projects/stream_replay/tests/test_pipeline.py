"""Тесты потоковых окон: дедупликация, закрытие по водяному знаку, опоздавшие."""

from __future__ import annotations

from projects.stream_replay.cli import main
from projects.stream_replay.pipeline import (
    Deduper,
    Event,
    StreamPipeline,
    WindowAggregator,
    generate_events,
)


def _event(event_id: str, ts: float, amount: float, client: str = "CL001") -> Event:
    return Event(event_id=event_id, ts=ts, client_id=client, amount=amount)


def test_window_closes_when_watermark_passes() -> None:
    aggregator = WindowAggregator(window_seconds=60, allowed_lateness=30)
    assert aggregator.add(_event("e1", 10, 100)) == []
    assert aggregator.add(_event("e2", 20, 50)) == []
    assert aggregator.add(_event("e3", 70, 10)) == []
    closed = aggregator.add(_event("e4", 100, 5))
    assert len(closed) == 1
    assert closed[0]["window_start"] == 0
    assert closed[0]["total_amount"] == 150.0
    assert closed[0]["events"] == 2


def test_flush_closes_remaining_windows() -> None:
    aggregator = WindowAggregator(window_seconds=60, allowed_lateness=30)
    aggregator.add(_event("e1", 10, 100))
    assert aggregator.closed == []
    flushed = aggregator.flush()
    assert len(flushed) == 1
    assert flushed[0]["total_amount"] == 100.0


def test_late_event_beyond_lateness_is_dropped() -> None:
    aggregator = WindowAggregator(window_seconds=60, allowed_lateness=30)
    aggregator.add(_event("e1", 100, 10))          # watermark = 70
    aggregator.add(_event("e2", 65, 999))          # опоздал слишком сильно
    assert aggregator.late_dropped == 1
    assert all(window["total_amount"] != 999 for window in aggregator.closed)


def test_late_event_within_lateness_lands_in_its_window() -> None:
    aggregator = WindowAggregator(window_seconds=60, allowed_lateness=30)
    aggregator.add(_event("e1", 100, 10))          # watermark = 70
    aggregator.add(_event("e2", 75, 500))          # окно [60,120), ещё открыто
    flushed = aggregator.flush()
    window = next(item for item in flushed if item["window_start"] == 60)
    assert window["total_amount"] == 510.0


def test_deduper_drops_repeats_and_caps_memory() -> None:
    deduper = Deduper(capacity=2)
    assert deduper.is_new("a") is True
    assert deduper.is_new("a") is False
    assert deduper.duplicates == 1
    assert deduper.is_new("b") is True
    assert deduper.is_new("c") is True          # вытесняет "a"
    assert deduper.is_new("a") is True          # "a" уже вне окна дедупликации


def test_at_least_once_equals_exactly_once_by_amounts() -> None:
    base = [_event(f"e{index}", index * 10, 100.0) for index in range(10)]
    doubled = base + base[:5]

    plain = StreamPipeline(window_seconds=60, allowed_lateness=10)
    plain.process(base)
    plain_result = plain.finish()

    at_least_once = StreamPipeline(window_seconds=60, allowed_lateness=10)
    at_least_once.process(doubled)
    alo_result = at_least_once.finish()

    assert at_least_once.metrics()["duplicates"] == 5
    assert alo_result["windows"] == plain_result["windows"]


def test_pipeline_metrics_for_generated_stream() -> None:
    events = generate_events(count=120, duplicate_ratio=0.2, late_ratio=0.05, seed=3)
    pipeline = StreamPipeline(window_seconds=60, allowed_lateness=30)
    pipeline.process(events)
    result = pipeline.finish()
    metrics = result["metrics"]
    assert metrics["received"] == len(events)
    assert metrics["duplicates"] >= 20
    assert metrics["windows_closed"] >= 1
    assert all(window["total_amount"] > 0 for window in result["windows"])


def test_cli_demo_runs(capsys) -> None:
    code = main(["demo", "--events", "60", "--json"])
    output = capsys.readouterr().out
    assert code == 0
    assert "окон закрыто:" in output
    assert "window_start" in output

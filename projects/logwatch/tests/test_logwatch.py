"""Тесты logwatch: парсинг, окна, дедупликация, экспорт метрик."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from projects.logwatch.cli import main
from projects.logwatch.export import render
from projects.logwatch.parser import LogRecord, parse_line, parse_stream
from projects.logwatch.rules import Rule, RuleEngine, default_rules

BASE = datetime(2026, 11, 6, 10, 0, tzinfo=timezone.utc)

NGINX = '10.0.0.5 - - [06/Nov/2026:10:00:00 +0300] "GET /api/clients/1 HTTP/1.1" 503 421 "-" "curl/8"'
JSON_LINE = (
    '{"ts": "2026-11-06T10:20:00+0300", "level": "error", "method": "POST", '
    '"path": "/api/transfer", "status": 500, "msg": "db pool exhausted", "duration_ms": 2310}'
)


def _record(offset: int, status: int = 500, path: str = "/api/x", level: str = "info") -> LogRecord:
    return LogRecord(
        raw=f"{status} {path}",
        ts=BASE + timedelta(seconds=offset),
        level=level,
        method="GET",
        path=path,
        status=status,
    )


def test_parse_nginx_line() -> None:
    record = parse_line(NGINX)
    assert record is not None
    assert record.status == 503
    assert record.path == "/api/clients/1"
    assert record.method == "GET"
    assert record.level == "error"
    assert record.ts is not None


def test_parse_json_line() -> None:
    record = parse_line(JSON_LINE)
    assert record is not None
    assert record.level == "error"
    assert record.status == 500
    assert record.duration_ms == 2310.0
    assert record.fields["msg"] == "db pool exhausted"


def test_unparsed_lines_are_skipped() -> None:
    assert parse_line("мусорная строка") is None
    assert parse_line("   ") is None
    assert parse_line("{не json}") is None
    assert len(parse_stream([NGINX, "мусор", "", JSON_LINE])) == 2


def test_five_xx_burst_requires_threshold() -> None:
    engine = RuleEngine([Rule(rule_id="5xx", severity="P1", status_at_least=500, min_count=3, window_seconds=60)])
    assert engine.feed(_record(0)) == []
    assert engine.feed(_record(1)) == []
    alerts = engine.feed(_record(2))
    assert len(alerts) == 1
    assert alerts[0].rule_id == "5xx"
    assert alerts[0].count == 3
    assert engine.counters["alerts"] == 1


def test_window_expires_old_hits() -> None:
    engine = RuleEngine([Rule(rule_id="5xx", min_count=3, window_seconds=60, status_at_least=500)])
    engine.feed(_record(0))
    engine.feed(_record(1))
    assert engine.feed(_record(120)) == []      # старые попадания вышли из окна
    assert engine.feed(_record(121)) == []
    assert len(engine.feed(_record(122))) == 1


def test_deduplication_suppresses_repeats() -> None:
    engine = RuleEngine([Rule(rule_id="5xx", min_count=1, window_seconds=60, dedup_seconds=300,
                              status_at_least=500)])
    first = engine.feed(_record(0))
    assert len(first) == 1
    assert engine.feed(_record(10)) == []
    assert engine.feed(_record(20)) == []
    assert engine.counters["suppressed"] == 2
    # после окна дедупликации алерт снова выпускается, с числом подавленных
    again = engine.feed(_record(400))
    assert len(again) == 1
    assert again[0].suppressed == 2


def test_level_and_regex_rules() -> None:
    engine = RuleEngine(default_rules())
    error_record = parse_line(JSON_LINE)
    alerts = engine.feed(error_record)
    rules = {alert.rule_id for alert in alerts}
    assert "app.error" in rules
    assert "db.pool_exhausted" in rules
    assert "http.slow_request" in rules
    assert all(alert.severity in {"P1", "P2", "P3"} for alert in alerts)


def test_export_contains_metrics() -> None:
    engine = RuleEngine(default_rules())
    alerts = engine.feed_all([parse_line(JSON_LINE), _record(5)])
    text = render(alerts, engine.rules, engine.counters)
    assert "# TYPE logwatch_alerts_total counter" in text
    assert 'logwatch_alerts_total{rule="db.pool_exhausted",severity="P1"} 1' in text
    assert "logwatch_events_total" in text
    assert 'logwatch_open_alerts{severity="P1"}' in text


def test_cli_demo_returns_failure_for_p1(tmp_path, capsys) -> None:
    prom = tmp_path / "logs" / "logwatch.prom"
    code = main(["analyze", "--demo", "--prom", str(prom)])
    output = capsys.readouterr().out
    assert code == 1
    assert "алертов:" in output
    assert prom.exists()
    assert "logwatch_alerts_total" in prom.read_text(encoding="utf-8")
